# Image Loading Thread Handshake

This diagram documents the thread-safe handshake between the image loader thread and the Qt/GL main thread to prevent race conditions during image load on startup.

```mermaid
sequenceDiagram
    participant User
    participant MainWindow
    participant ImageLoader as Loader Thread
    participant ImageWidgetGL as Qt/GL Thread
    participant OffscreenContext as Shared GL Context
    
    User->>MainWindow: Open Image (file association)
    MainWindow->>ImageLoader: load_image()
    activate ImageLoader
    ImageLoader->>ImageLoader: Decode JPEG (worker thread)
    note over ImageLoader: Non-blocking decode
    deactivate ImageLoader
    
    activate ImageWidgetGL
    ImageWidgetGL->>ImageWidgetGL: initializeGL()
    ImageWidgetGL->>OffscreenContext: init_gl()
    note over OffscreenContext: Setup shared context<br/>(deferred with QTimer)
    ImageWidgetGL->>ImageLoader: context_created(OffscreenContext)
    deactivate ImageWidgetGL
    
    activate ImageLoader
    ImageLoader->>OffscreenContext: makeCurrent(surface)
    ImageLoader->>OffscreenContext: texture.initialize_gl()
    note over OffscreenContext: GPU upload in worker thread
    ImageLoader->>OffscreenContext: doneCurrent()
    ImageLoader->>MainWindow: texture_created(ImageData)
    deactivate ImageLoader
    
    MainWindow->>ImageWidgetGL: set_image_data(ImageData)
    ImageWidgetGL->>User: Display Image
```

## Key Points

- **Loader Thread**: Handles JPEG decoding and GL texture upload asynchronously
- **Shared Offscreen Context**: Allows the worker thread to perform GL operations safely
- **Signal Handshake**: `loader_context_created` signal passes the shared context to the loader thread
- **Context Manager**: The offscreen context uses `__enter__`/`__exit__` for proper makeCurrent/doneCurrent management
- **No Main Thread Blocking**: All heavy operations (decode + GPU upload) happen in the worker thread
- **Race Condition Prevention**: The context is only used after it's initialized and the signal confirms it's ready

## Related Code Files

- [vmg/image_loader.py](../vmg/image_loader.py) - Handles image decoding
- [vmg/main_window.py](../vmg/ui/main_window.py) - Coordinates GL uploads
- [vmg/image_widget_gl.py](../vmg/ui/image_widget_gl.py) - Manages GL context and rendering
