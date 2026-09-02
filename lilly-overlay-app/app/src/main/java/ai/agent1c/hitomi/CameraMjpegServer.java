package ai.agent1c.hitomi;

import android.util.Log;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

public class CameraMjpegServer {
    private static final String TAG = "CameraMjpeg";
    private static final int PORT = 8095;
    private static final String BOUNDARY = "frame_boundary";

    private final ArrayBlockingQueue<byte[]> frameQueue = new ArrayBlockingQueue<>(4);
    private volatile boolean running = false;
    private Thread acceptThread;
    private ServerSocket serverSocket;

    public void startServer() {
        if (running) return;
        running = true;
        acceptThread = new Thread(() -> {
            try {
                serverSocket = new ServerSocket(PORT);
                Log.i(TAG, "Camera MJPEG server started on port " + PORT);

                while (running) {
                    try {
                        Socket client = serverSocket.accept();
                        if (!running) break;
                        handleClient(client);
                    } catch (IOException e) {
                        if (running) Log.e(TAG, "Accept error", e);
                    }
                }
            } catch (IOException e) {
                Log.e(TAG, "Failed to start camera server", e);
            }
        }, "CameraMjpegServer");
        acceptThread.start();
    }

    public void stopServer() {
        if (!running) return;
        running = false;
        frameQueue.clear();
        try {
            if (serverSocket != null && !serverSocket.isClosed()) {
                serverSocket.close();
            }
        } catch (IOException e) {
            Log.e(TAG, "Error stopping server", e);
        }
        if (acceptThread != null) {
            try {
                acceptThread.join(1000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }

    public void pushFrame(byte[] jpegData) {
        if (!running || jpegData == null || jpegData.length == 0) return;
        frameQueue.offer(jpegData);
        if (frameQueue.size() > 3) {
            frameQueue.poll();
        }
    }

    private void handleClient(Socket client) {
        new Thread(() -> {
            try {
                client.setTcpNoDelay(true);
                OutputStream out = client.getOutputStream();
                String header = "HTTP/1.0 200 OK\r\n" +
                    "Content-Type: multipart/x-mixed-replace; boundary=" + BOUNDARY + "\r\n" +
                    "Connection: close\r\n\r\n";
                out.write(header.getBytes(java.nio.charset.StandardCharsets.US_ASCII));
                out.flush();

                while (running && !client.isClosed()) {
                    byte[] frame;
                    try {
                        frame = frameQueue.poll(100, TimeUnit.MILLISECONDS);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                    if (frame == null) continue;

                    String partHeader = "--" + BOUNDARY + "\r\n" +
                        "Content-Type: image/jpeg\r\n" +
                        "Content-Length: " + frame.length + "\r\n\r\n";
                    out.write(partHeader.getBytes(java.nio.charset.StandardCharsets.US_ASCII));
                    out.write(frame);
                    out.write("\r\n".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
                    out.flush();
                }
            } catch (IOException e) {
                // Client disconnected
            } finally {
                try {
                    client.close();
                } catch (IOException e) {
                    // ignore
                }
            }
        }, "CameraMjpegClient").start();
    }
}
