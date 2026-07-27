package com.lilly.assistant.services

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.lilly.assistant.R
import com.lilly.assistant.ui.MainActivity
import kotlinx.coroutines.*

class LillyForegroundService : Service() {
    
    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    
    override fun onBind(intent: Intent?): IBinder? {
        return null
    }
    
    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }
    
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startForegroundService()
            ACTION_STOP -> stopForegroundService()
            ACTION_PAUSE -> pauseService()
            ACTION_RESUME -> resumeService()
        }
        
        return START_STICKY
    }
    
    private fun startForegroundService() {
        val notification = createNotification("Lilly is running in the background")
        startForeground(NOTIFICATION_ID, notification)
        
        // Start background tasks
        scope.launch {
            // Perform background operations
        }
    }
    
    private fun stopForegroundService() {
        stopForeground(true)
        stopSelf()
    }
    
    private fun pauseService() {
        val notification = createNotification("Lilly is paused")
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(NOTIFICATION_ID, notification)
    }
    
    private fun resumeService() {
        val notification = createNotification("Lilly is running in the background")
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(NOTIFICATION_ID, notification)
    }
    
    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Lilly Service",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Lilly assistant service"
            }
            
            val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notificationManager.createNotificationChannel(channel)
        }
    }
    
    private fun createNotification(text: String): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, LillyForegroundService::class.java).apply {
                action = ACTION_STOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        
        val pauseIntent = PendingIntent.getService(
            this,
            2,
            Intent(this, LillyForegroundService::class.java).apply {
                action = ACTION_PAUSE
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Lilly Assistant")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(pendingIntent)
            .addAction(R.drawable.ic_pause, "Pause", pauseIntent)
            .addAction(R.drawable.ic_stop, "Stop", stopIntent)
            .setOngoing(true)
            .build()
    }
    
    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
    
    companion object {
        private const val CHANNEL_ID = "lilly_service_channel"
        private const val NOTIFICATION_ID = 1
        
        const val ACTION_START = "com.lilly.assistant.START"
        const val ACTION_STOP = "com.lilly.assistant.STOP"
        const val ACTION_PAUSE = "com.lilly.assistant.PAUSE"
        const val ACTION_RESUME = "com.lilly.assistant.RESUME"
        
        fun startService(context: Context) {
            val intent = Intent(context, LillyForegroundService::class.java).apply {
                action = ACTION_START
            }
            context.startForegroundService(intent)
        }
        
        fun stopService(context: Context) {
            val intent = Intent(context, LillyForegroundService::class.java).apply {
                action = ACTION_STOP
            }
            context.startService(intent)
        }
    }
}