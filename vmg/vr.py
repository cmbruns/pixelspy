from contextlib import ExitStack
from ctypes import byref, cast, POINTER
import logging
import sys
import time

from OpenGL import GL
from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel
import xr

from vmg.resources import resource_filename

if sys.platform == "win32":
    from OpenGL import WGL
else:
    from OpenGL import GLX

from vmg.offscreen_context import OffscreenContext
from vmg.version import __version__ as app_version

logger = logging.getLogger(__name__)


class SessionStateEventHandler:
    def __init__(self, session, view_configuration_type: xr.ViewConfigurationType):
        self.session = session
        self.view_configuration_type = view_configuration_type
        self.exit_render_loop = False
        self.request_restart = False
        self.session_state = xr.SessionState.IDLE
        self.session_is_running = False

    def handle_event(self, event_buffer: xr.EventDataBuffer):
        event_type = xr.StructureType(event_buffer.type)
        if event_type == xr.StructureType.EVENT_DATA_INSTANCE_LOSS_PENDING:
            self.exit_render_loop = True
            self.request_restart = True
        elif event_type == xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED:
            event = cast(
                byref(event_buffer),
                POINTER(xr.EventDataSessionStateChanged)
            ).contents
            self.session_state = xr.SessionState(event.state)
            logger.info(f"OpenXR session state changed to {self.session_state.name}")
            if self.session_state == xr.SessionState.READY:
                xr.begin_session(
                    session=self.session,
                    begin_info=xr.SessionBeginInfo(self.view_configuration_type)
                )
                self.session_is_running = True
            elif self.session_state == xr.SessionState.STOPPING:
                self.session_is_running = False
                xr.end_session(self.session)
            elif self.session_state in (xr.SessionState.EXITING, xr.SessionState.LOSS_PENDING):
                self.exit_render_loop = True
                self.request_restart = (self.session_state == xr.SessionState.LOSS_PENDING)


class VRThing(QObject):
    def __init__(self, parent: QObject = None):
        super().__init__(parent=parent)
        self.exit_stack: ExitStack = ExitStack()
        self.instance: xr.Instance = None
        self.system_id: xr.SystemId = None
        self.render_target_size = (1, 1)
        self.offscreen_context = None
        self.session = None
        self.space = None
        self.view_configuration_type = None
        self.swapchain_sizes = None
        self.swapchain_image_ptr_buffers = None
        self.swapchain_framebuffer = None
        self.swapchains = None
        self.swapchain_images = None
        self.blend_mode = None
        self.action_set = None
        self.exit_action = None
        self.swapchain_image_type = xr.SwapchainImageOpenGLKHR

    @Slot()
    def enter_vr(self):
        logger.info("Entering VR")
        self.vr_session_waiting.emit()
        # print("Starting SteamVR")
        # QDesktopServices.openUrl(QUrl("steam://run/250820"))
        try:
            self.init_xr()
            logger.info("VR Session Started...")
            self.vr_session_started.emit()
            self.xr_loop()
            logger.info("VR Session Exiting...")
            self.vr_session_exited.emit()
        except xr.exception.RuntimeFailureError:
            logger.warning("Failed to create OpenXR Instance")
            self.vr_session_failed.emit("Unable to create OpenXR Instance.\nIs your headset connected and working?")
        except BaseException as exc:
            logger.warning(f"VR Error {exc}")
            self.vr_session_failed.emit(f"{exc}")
        finally:
            logger.info("Cleaning up VR Session Remnants...")
            self.exit_stack.close()

    def init_xr(self) -> bool:
        if self.offscreen_context is None:
            raise RuntimeError("Missing OpenGL context for VR")
        # 1) XR Instance
        if "XR_KHR_opengl_enable" not in xr.enumerate_instance_extension_properties():
            raise RuntimeError("Required OpenXR extension 'XR_KHR_opengl_enable' is not available")
        major, minor, patch = [int(x) for x in app_version.split(".")]
        logger.debug("Creating Instance")
        self.instance = self.exit_stack.enter_context(xr.create_instance(
            xr.InstanceCreateInfo(
                application_info=xr.ApplicationInfo(
                    application_name=QGuiApplication.applicationDisplayName(),
                    application_version=(major << 16) | (minor << 8) | patch,
                ),
                enabled_extension_names=["XR_KHR_opengl_enable"],
            ),
        ))
        # 2) XR System
        self.system_id = xr.get_system(self.instance, xr.SystemGetInfo(
            form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY,
        ))
        view_configs = xr.enumerate_view_configurations(self.instance, self.system_id)
        assert view_configs[0] == xr.ViewConfigurationType.PRIMARY_STEREO.value
        view_config_views = xr.enumerate_view_configuration_views(
            self.instance, self.system_id, xr.ViewConfigurationType.PRIMARY_STEREO)
        assert len(view_config_views) == 2
        assert view_config_views[0].recommended_image_rect_height == view_config_views[1].recommended_image_rect_height
        self.render_target_size = (
            view_config_views[0].recommended_image_rect_width * 2,
            view_config_views[0].recommended_image_rect_height)
        self.offscreen_context.make_current()
        _graphics_requirements = xr.get_opengl_graphics_requirements_khr(self.instance, self.system_id)
        if sys.platform == "win32":
            graphics_binding = xr.GraphicsBindingOpenGLWin32KHR(
                h_dc=WGL.wglGetCurrentDC(),
                h_glrc=WGL.wglGetCurrentContext(),
            )
        else:
            graphics_binding = xr.GraphicsBindingOpenGLXlibKHR(
                x_display=GLX.glXGetCurrentDisplay(),
                glx_context=GLX.glXGetCurrentContext(),
                glx_drawable=GLX.glXGetCurrentDrawable(),
            )
        logger.debug("Creating Session")
        self.session = self.exit_stack.enter_context(xr.create_session(
            self.instance,
            xr.SessionCreateInfo(
                system_id=self.system_id,
                next=graphics_binding,
            ),
        ))
        # blend mode
        self.blend_mode = None
        blend_modes = list(xr.enumerate_environment_blend_modes(
            self.instance, self.system_id, xr.ViewConfigurationType.PRIMARY_STEREO))
        for b in [xr.EnvironmentBlendMode.ALPHA_BLEND, xr.EnvironmentBlendMode.OPAQUE]:
            if b in blend_modes:
                self.blend_mode = b
                break
        if self.blend_mode is None:
            self.blend_mode = blend_modes[0]
        # reference space
        self.space = self.exit_stack.enter_context(xr.create_reference_space(
            self.session,
            xr.ReferenceSpaceCreateInfo(xr.ReferenceSpaceType.STAGE)
        ))
        # action set
        self.action_set = self.exit_stack.enter_context(xr.create_action_set(
            instance=self.instance,
            create_info=xr.ActionSetCreateInfo(
                action_set_name="action_set",
                localized_action_set_name="Action Set",
                priority=0,
            ),
        ))
        self.set_up_actions()
        # swapchain format
        logger.debug("Creating Swapchains")
        color_swapchain_format: int = None
        swapchain_formats = xr.enumerate_swapchain_formats(self.session)
        for sf in [GL.GL_RGBA8, GL.GL_RGBA8_SNORM, GL.GL_SRGB8_ALPHA8]:
            if sf in swapchain_formats:
                color_swapchain_format = sf
                break
        assert color_swapchain_format is not None
        logger.debug(f"Swapchain color format = {repr(color_swapchain_format)}")
        # views (usually two: one for the left eye; one for the right)
        self.view_configuration_type = xr.ViewConfigurationType.PRIMARY_STEREO
        config_views = xr.enumerate_view_configuration_views(
            instance=self.instance,
            system_id=self.system_id,
            view_configuration_type=self.view_configuration_type,
        )
        logger.debug(f"{len(config_views)} view configuration views found")
        assert len(config_views) > 0
        # create a swapchain for each view
        self.swapchains = []
        self.swapchain_images = []
        self.swapchain_sizes = []
        self.swapchain_image_ptr_buffers = []
        for v in config_views:
            logger.debug(f"Creating swapchain for view {v}")
            self.swapchains.append(xr.create_swapchain(self.session, xr.SwapchainCreateInfo(
                array_size=1,
                format=color_swapchain_format,
                width=v.recommended_image_rect_width,
                height=v.recommended_image_rect_height,
                mip_count=1,
                face_count=1,
                sample_count=1,
                usage_flags=xr.SwapchainUsageFlags.SAMPLED_BIT | xr.SwapchainUsageFlags.COLOR_ATTACHMENT_BIT,
            )))
            logger.debug(f"Finished creating swapchain for view {v}")
            self.swapchain_images.append(xr.enumerate_swapchain_images(
                swapchain=self.swapchains[-1], element_type=self.swapchain_image_type))
            logger.debug(f"Finished enumerating swapchain images")
            self.swapchain_sizes.append((v.recommended_image_rect_width, v.recommended_image_rect_height))
            num_images = len(self.swapchain_images[-1])
            swapchain_image_ptr_buffer = (POINTER(xr.SwapchainImageBaseHeader) * num_images)()
            logger.debug(f"Creating swapchain image pointers")
            for ix in range(num_images):
                swapchain_image_ptr_buffer[ix] = cast(
                    byref(self.swapchain_images[-1][ix]),
                    POINTER(xr.SwapchainImageBaseHeader))
            self.swapchain_image_ptr_buffers.append(swapchain_image_ptr_buffer)
        # framebuffer
        logger.debug("Creating Framebuffer")
        self.swapchain_framebuffer = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.swapchain_framebuffer)
        # action sets
        logger.debug("Attaching Action Set")
        xr.attach_session_action_sets(self.session, attach_info=xr.SessionActionSetsAttachInfo(
            action_sets=[self.action_set, ],
        ))
        return True

    @Slot(OffscreenContext)  # noqa
    def on_context_created(self, offscreen_context: OffscreenContext) -> None:
        logger.info("Received new VR OpenGL context.")
        assert self.offscreen_context is None
        self.offscreen_context = offscreen_context

    vr_session_waiting = Signal()
    vr_session_started = Signal()
    vr_session_failed = Signal(str)
    vr_session_exited = Signal()

    def poll_actions(self):
        logger.debug("Setting up actions")
        active_action_set = xr.ActiveActionSet(self.action_set, xr.NULL_PATH)
        xr.sync_actions(
            self.session,
            xr.ActionsSyncInfo(
                active_action_sets=[active_action_set]
            ),
        )
        state = xr.get_action_state_boolean(
            self.session,
            xr.ActionStateGetInfo(
                action=self.exit_action,
            ),
        )
        if state.is_active and state.changed_since_last_sync and state.current_state:
            xr.request_exit_session(self.session)
        #

    def set_up_actions(self):
        self.exit_action = xr.create_action(
            action_set=self.action_set,
            create_info=xr.ActionCreateInfo(
                action_name="exit_vr",
                localized_action_name="Exit VR",
                action_type=xr.ActionType.BOOLEAN_INPUT,
            ),
        )
        xr.suggest_interaction_profile_bindings(
            instance=self.instance,
            suggested_bindings=xr.InteractionProfileSuggestedBinding(
                interaction_profile=xr.string_to_path(
                    self.instance,
                    "/interaction_profiles/oculus/touch_controller",
                ),
                suggested_bindings=[
                    xr.ActionSuggestedBinding(
                        self.exit_action,
                        xr.string_to_path(self.instance, "/user/hand/left/input/y/click"),
                    ),
                    xr.ActionSuggestedBinding(
                        self.exit_action,
                        xr.string_to_path(self.instance, "/user/hand/right/input/b/click"),
                    ),
                ],
            ),
        )

    def xr_loop(self):
        event_handler = SessionStateEventHandler(self.session, self.view_configuration_type)
        color_to_depth_map = dict()
        for _ in range(5000):
            # TODO: poll android events
            # Poll session state events
            while True:
                try:
                    event_buffer = xr.poll_event(self.instance)
                    event_handler.handle_event(event_buffer)
                except xr.EventUnavailable:
                    break
            if event_handler.exit_render_loop:
                break
            if (event_handler.session_is_running and
                    event_handler.session_state in (
                            xr.SessionState.READY,
                            xr.SessionState.SYNCHRONIZED,
                            xr.SessionState.VISIBLE,
                            xr.SessionState.FOCUSED,
                    )):
                try:
                    self.poll_actions()
                except xr.SessionNotFocused:
                    pass
                frame_state = xr.wait_frame(self.session)
                xr.begin_frame(self.session)
                layers = []
                if frame_state.should_render:
                    assert GL.glGetError() == GL.GL_NO_ERROR
                    layer = xr.CompositionLayerProjection(space=self.space)
                    view_state, views = xr.locate_views(self.session, xr.ViewLocateInfo(
                        view_configuration_type=self.view_configuration_type,
                        display_time=frame_state.predicted_display_time,
                        space=self.space,
                    ))
                    projection_layer_views = tuple(xr.CompositionLayerProjectionView() for _ in range(len(views)))
                    vsf = view_state.view_state_flags
                    poses_are_valid = (vsf & xr.VIEW_STATE_POSITION_VALID_BIT) and (vsf & xr.VIEW_STATE_ORIENTATION_VALID_BIT)
                    assert GL.glGetError() == GL.GL_NO_ERROR
                    if poses_are_valid:
                        for view_index, view in enumerate(views):
                            assert GL.glGetError() == GL.GL_NO_ERROR
                            swapchain = self.swapchains[view_index]
                            swapchain_image_index = xr.acquire_swapchain_image(
                                swapchain=swapchain,
                                acquire_info=xr.SwapchainImageAcquireInfo(),
                            )
                            xr.wait_swapchain_image(
                                swapchain=swapchain,
                                wait_info=xr.SwapchainImageWaitInfo(timeout=xr.INFINITE_DURATION),
                            )
                            layer_view = projection_layer_views[view_index]
                            assert layer_view.type == xr.StructureType.COMPOSITION_LAYER_PROJECTION_VIEW
                            layer_view.pose = view.pose
                            layer_view.fov = view.fov
                            layer_view.sub_image.swapchain = swapchain
                            layer_view.sub_image.image_rect.offset[:] = [0, 0]
                            layer_view.sub_image.image_rect.extent[:] = [*self.swapchain_sizes[view_index]]
                            swapchain_image_ptr = self.swapchain_image_ptr_buffers[view_index][swapchain_image_index]
                            swapchain_image = cast(swapchain_image_ptr, POINTER(self.swapchain_image_type)).contents
                            assert layer_view.sub_image.image_array_index == 0  # texture arrays not supported.
                            color_texture = swapchain_image.image
                            # graphics begin frame
                            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.swapchain_framebuffer)
                            GL.glViewport(layer_view.sub_image.image_rect.offset.x,
                                          layer_view.sub_image.image_rect.offset.y,
                                          layer_view.sub_image.image_rect.extent.width,
                                          layer_view.sub_image.image_rect.extent.height)
                            if color_texture in color_to_depth_map:
                                depth_texture = color_to_depth_map[color_texture]
                            else:
                                GL.glBindTexture(GL.GL_TEXTURE_2D, color_texture)
                                width = GL.glGetTexLevelParameteriv(GL.GL_TEXTURE_2D, 0, GL.GL_TEXTURE_WIDTH)
                                height = GL.glGetTexLevelParameteriv(GL.GL_TEXTURE_2D, 0, GL.GL_TEXTURE_HEIGHT)
                                depth_texture = GL.glGenTextures(1)
                                GL.glBindTexture(GL.GL_TEXTURE_2D, depth_texture)
                                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
                                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
                                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
                                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
                                if sys.platform == "android":  # or OpenGLES really
                                    depth_component = GL.GL_DEPTH_COMPONENT24
                                else:
                                    depth_component = GL.GL_DEPTH_COMPONENT32
                                GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, depth_component, width, height, 0,
                                                GL.GL_DEPTH_COMPONENT, GL.GL_UNSIGNED_INT, None)
                                color_to_depth_map[color_texture] = depth_texture
                            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D,
                                                      color_texture, 0)
                            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_TEXTURE_2D,
                                                      depth_texture, 0)

                            self.render_frame(frame_state, view)
                            # yield frame_state, view

                            # render - paint the entire universe a pale pink color
                            # GL.glClearColor(1, 0.7, 0.7, 1)  # pink
                            # GL.glClear(GL.GL_COLOR_BUFFER_BIT)

                            # end frame
                            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
                            xr.release_swapchain_image(
                                swapchain=swapchain,
                                release_info=xr.SwapchainImageReleaseInfo())
                        layer.views = projection_layer_views
                        layers.append(byref(layer))
                assert GL.glGetError() == GL.GL_NO_ERROR  # Prepare to avoid Linux SteamVR bug
                xr.end_frame(self.session, xr.FrameEndInfo(
                    display_time=frame_state.predicted_display_time,
                    environment_blend_mode=self.blend_mode,
                    layers=layers,
                ))
                GL.glGetError()  # Clear GL error state to avoid Linux SteamVR bug
            else:
                # throttle loop since xr.wait_frame() won't be called
                time.sleep(0.250)
        # end of frame loop
        # wind down session state
        try:
            xr.request_exit_session(self.session)
        except xr.SessionNotRunningError:
            pass
        for _ in range(20):
            while True:
                try:
                    event_buffer = xr.poll_event(self.instance)
                    event_handler.handle_event(event_buffer)
                except xr.EventUnavailable:
                    break
            if event_handler.exit_render_loop:
                break
            if (event_handler.session_is_running and
                    event_handler.session_state in (
                            xr.SessionState.READY,
                            xr.SessionState.SYNCHRONIZED,
                            xr.SessionState.VISIBLE,
                            xr.SessionState.FOCUSED,
                    )):
                frame_state = xr.wait_frame(self.session)
                xr.begin_frame(self.session)
                time.sleep(0.050)  # Yield time for other subsystems
                xr.end_frame(
                    self.session,
                    frame_end_info=xr.FrameEndInfo(
                        display_time=frame_state.predicted_display_time,
                        environment_blend_mode=self.blend_mode,
                        layers=[],
                    )
                )

    def render_frame(self, frame_state: xr.FrameState, view: xr.View):
        GL.glClearColor(0.7, 1.0, 0.7, 1)  # pale green
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        # TODO: render a rectangle in the world/stage frame


class VrStateIndicator(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.setContentsMargins(0, 0, 0, 0)
        self.setScaledContents(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pixmap_active = QPixmap(resource_filename("vmg.images", "hmd_green32.png"))
        self.pixmap_waiting = QPixmap(resource_filename("vmg.images", "hmd_waiting32.png"))
        self.pixmap_failed = QPixmap(resource_filename("vmg.images", "hmd_error_x32.png"))
        # Set up the 20-second single-shot failure timer
        self.failure_timer = QTimer(self)
        self.failure_timer.setSingleShot(True)
        self.failure_timer.timeout.connect(self.set_idle_state)
        # Initialize
        self.set_idle_state()

    @Slot()
    def set_active_state(self):
        """Slots to handle a successful VR initialization."""
        self.failure_timer.stop()
        self.setPixmap(self.pixmap_active)
        self.setScaledContents(True)
        self.setToolTip("VR Session Active")
        self.show()

    @Slot(str)
    def set_failed_state(self, error_message: str = ""):
        """Slots to handle a VR initialization failure."""
        self.setPixmap(self.pixmap_failed)
        self.setScaledContents(True)
        self.setToolTip(f"VR Error: {error_message}" if error_message else "VR Initialization Failed")
        self.show()

        # Start the 20-second fade out back to idle
        self.failure_timer.start(20000)

    @Slot()
    def set_idle_state(self):
        """Resets back to the neutral disconnected icon."""
        self.hide()

    @Slot()
    def set_waiting_state(self):
        """Slots to handle a VR initialization failure."""
        self.setPixmap(self.pixmap_waiting)
        self.setScaledContents(True)
        self.setToolTip(f"VR Session Starting...")
        self.show()
