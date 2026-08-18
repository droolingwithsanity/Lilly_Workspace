package ai.agent1c.hitomi;

import android.content.Context;
import android.graphics.ImageFormat;
import android.graphics.YuvImage;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CameraMetadata;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.TotalCaptureResult;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;
import android.util.Size;
import android.view.Surface;

import androidx.annotation.NonNull;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.List;

public class CameraStreamBridge {
    private static final String TAG = "CameraStream";
    private static final int MAX_FRAME_SIZE = 640 * 480 * 3 / 2; // ~460KB for 640x480 NV21
    private static final int FRAME_FORMAT = ImageFormat.YUV_420_888;

    private final Context context;
    private CameraDevice cameraDevice;
    private CameraCaptureSession captureSession;
    private ImageReader imageReader;
    private Handler backgroundHandler;
    private HandlerThread backgroundThread;
    private String cameraId;
    private boolean streaming = false;
    private OnFrameCallback frameCallback;

    public interface OnFrameCallback {
        void onFrame(byte[] jpegData, int width, int height);
    }

    public CameraStreamBridge(Context context) {
        this.context = context.getApplicationContext();
    }

    public void setFrameCallback(OnFrameCallback callback) {
        this.frameCallback = callback;
    }

    public boolean startStreaming() {
        if (streaming) return true;

        CameraManager manager = (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
        if (manager == null) {
            Log.e(TAG, "Camera manager unavailable");
            return false;
        }

        try {
            String[] cameraIds = manager.getCameraIdList();
            cameraId = null;
            for (String id : cameraIds) {
                CameraCharacteristics characteristics = manager.getCameraCharacteristics(id);
                Integer facing = characteristics.get(CameraCharacteristics.LENS_FACING);
                if (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK) {
                    cameraId = id;
                    break;
                }
            }
            if (cameraId == null && cameraIds.length > 0) {
                cameraId = cameraIds[0];
            }

            if (cameraId == null) {
                Log.e(TAG, "No camera available");
                return false;
            }

            CameraCharacteristics characteristics = manager.getCameraCharacteristics(cameraId);
            StreamConfigurationMap map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            Size[] sizes = map.getOutputSizes(ImageFormat.YUV_420_888);
            Size previewSize = sizes[0];
            for (Size size : sizes) {
                if (size.getWidth() <= 640 && size.getHeight() <= 480) {
                    previewSize = size;
                    break;
                }
            }

            imageReader = ImageReader.newInstance(previewSize.getWidth(), previewSize.getHeight(), FRAME_FORMAT, 2);
            imageReader.setOnImageAvailableListener(this::onImageAvailable, backgroundHandler);

            startBackgroundThread();
            manager.openCamera(cameraId, stateCallback, backgroundHandler);
            return true;

        } catch (CameraAccessException | SecurityException e) {
            Log.e(TAG, "Camera access failed", e);
            return false;
        }
    }

    public void stopStreaming() {
        streaming = false;
        if (captureSession != null) {
            try {
                captureSession.stopRepeating();
                captureSession.close();
            } catch (CameraAccessException e) {
                Log.e(TAG, "Error closing capture session", e);
            }
            captureSession = null;
        }
        if (cameraDevice != null) {
            cameraDevice.close();
            cameraDevice = null;
        }
        if (imageReader != null) {
            imageReader.close();
            imageReader = null;
        }
        stopBackgroundThread();
    }

    private final CameraDevice.StateCallback stateCallback = new CameraDevice.StateCallback() {
        @Override
        public void onOpened(@NonNull CameraDevice camera) {
            cameraDevice = camera;
            try {
                createCaptureSession();
            } catch (CameraAccessException e) {
                Log.e(TAG, "Failed to create capture session", e);
            }
        }

        @Override
        public void onDisconnected(@NonNull CameraDevice camera) {
            camera.close();
            cameraDevice = null;
        }

        @Override
        public void onError(@NonNull CameraDevice camera, int error) {
            Log.e(TAG, "Camera error: " + error);
            camera.close();
            cameraDevice = null;
        }
    };

    private void createCaptureSession() throws CameraAccessException {
        if (cameraDevice == null || imageReader == null) return;

        Surface surface = imageReader.getSurface();
        List<Surface> surfaces = new ArrayList<>();
        surfaces.add(surface);

        cameraDevice.createCaptureSession(surfaces, new CameraCaptureSession.StateCallback() {
            @Override
            public void onConfigured(@NonNull CameraCaptureSession session) {
                captureSession = session;
                try {
                    CaptureRequest.Builder builder = cameraDevice.createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
                    builder.addTarget(surface);
                    builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
                    builder.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON);
                    captureSession.setRepeatingRequest(builder.build(), null, backgroundHandler);
                    streaming = true;
                    Log.i(TAG, "Camera streaming started");
                } catch (CameraAccessException e) {
                    Log.e(TAG, "Failed to start preview", e);
                }
            }

            @Override
            public void onConfigureFailed(@NonNull CameraCaptureSession session) {
                Log.e(TAG, "Capture session configuration failed");
            }
        }, backgroundHandler);
    }

    private void onImageAvailable(ImageReader reader) {
        if (!streaming) return;

        Image image = null;
        try {
            image = reader.acquireLatestImage();
            if (image == null) return;

            byte[] jpeg = yuv420ToJpeg(image);
            if (frameCallback != null && jpeg != null) {
                frameCallback.onFrame(jpeg, image.getWidth(), image.getHeight());
            }
        } catch (Exception e) {
            Log.e(TAG, "Frame capture error", e);
        } finally {
            if (image != null) {
                image.close();
            }
        }
    }

    private byte[] yuv420ToJpeg(Image image) {
        if (image == null) return null;

        Image.Plane[] planes = image.getPlanes();
        if (planes == null || planes.length != 3) return null;

        int width = image.getWidth();
        int height = image.getHeight();
        int ySize = width * height;
        int uvSize = width * height / 2;
        byte[] nv21 = new byte[ySize + uvSize];

        ByteBuffer yBuffer = planes[0].getBuffer();
        ByteBuffer uBuffer = planes[1].getBuffer();
        ByteBuffer vBuffer = planes[2].getBuffer();

        int rowStrideY = planes[0].getRowStride();
        int rowStrideU = planes[1].getRowStride();
        int rowStrideV = planes[2].getRowStride();

        int pos = 0;

        // Y plane
        for (int row = 0; row < height; row++) {
            yBuffer.position(row * rowStrideY);
            for (int col = 0; col < width; col++) {
                nv21[pos++] = yBuffer.get();
            }
        }

        // VU interleaved
        int chromaHeight = height / 2;
        int chromaWidth = width / 2;

        for (int row = 0; row < chromaHeight; row++) {
            vBuffer.position(row * rowStrideV);
            uBuffer.position(row * rowStrideU);
            for (int col = 0; col < chromaWidth; col++) {
                nv21[pos++] = vBuffer.get();
                nv21[pos++] = uBuffer.get();
            }
        }

        try {
            YuvImage yuvImage = new YuvImage(nv21, ImageFormat.NV21, width, height, null);
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            yuvImage.compressToJpeg(new android.graphics.Rect(0, 0, width, height), 70, out);
            return out.toByteArray();
        } catch (Exception e) {
            Log.e(TAG, "JPEG conversion failed", e);
            return null;
        }
    }

    private void startBackgroundThread() {
        backgroundThread = new HandlerThread("CameraBackground");
        backgroundThread.start();
        backgroundHandler = new Handler(backgroundThread.getLooper());
    }

    private void stopBackgroundThread() {
        if (backgroundThread != null) {
            backgroundThread.quitSafely();
            try {
                backgroundThread.join();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            backgroundThread = null;
            backgroundHandler = null;
        }
    }
}
