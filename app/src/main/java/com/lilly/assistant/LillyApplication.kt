package com.lilly.assistant

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.lilly.assistant.core.LillyCore
import com.lilly.assistant.core.MemoryManager
import com.lilly.assistant.core.SystemPromptManager
import com.lilly.assistant.core.CursorController
import com.lilly.assistant.core.RealtimeProcessor
import com.lilly.assistant.core.SkillManager
import com.lilly.assistant.core.LillyPackageManager
import com.lilly.assistant.database.LillyDatabase

class LillyApplication : Application() {
    
    lateinit var database: LillyDatabase
        private set
    
    lateinit var core: LillyCore
        private set
    
    lateinit var memoryManager: MemoryManager
        private set
    
    lateinit var systemPromptManager: SystemPromptManager
        private set
    
    lateinit var cursorController: CursorController
        private set
    
    lateinit var realtimeProcessor: RealtimeProcessor
        private set
    
    lateinit var skillManager: SkillManager
        private set
    
    lateinit var packageManager: LillyPackageManager
        private set
    
    override fun onCreate() {
        super.onCreate()
        instance = this
        
        initializeDatabase()
        initializeManagers()
        initializeCore()
        createNotificationChannels()
    }
    
    private fun initializeDatabase() {
        database = LillyDatabase.getInstance(this)
    }
    
    private fun initializeManagers() {
        memoryManager = MemoryManager(database.memoryDao())
        systemPromptManager = SystemPromptManager(this)
        cursorController = CursorController(this)
        realtimeProcessor = RealtimeProcessor()
        skillManager = SkillManager(this, database.skillDao())
        packageManager = LillyPackageManager(this, database.skillDao())
    }
    
    private fun initializeCore() {
        core = LillyCore(
            database = database,
            memoryManager = memoryManager,
            systemPromptManager = systemPromptManager,
            cursorController = cursorController,
            realtimeProcessor = realtimeProcessor,
            skillManager = skillManager,
            packageManager = packageManager
        )
    }
    
    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val serviceChannel = NotificationChannel(
                SERVICE_CHANNEL_ID,
                "Lilly Service",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Lilly assistant background service"
            }
            
            val notificationChannel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "Lilly Notifications",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Lilly assistant notifications"
            }
            
            val notificationManager = getSystemService(NotificationManager::class.java)
            notificationManager.createNotificationChannel(serviceChannel)
            notificationManager.createNotificationChannel(notificationChannel)
        }
    }
    
    companion object {
        const val SERVICE_CHANNEL_ID = "lilly_service_channel"
        const val NOTIFICATION_CHANNEL_ID = "lilly_notification_channel"
        
        @Volatile
        private var _instance: LillyApplication? = null
        
        val instance: LillyApplication
            get() = _instance ?: throw IllegalStateException("Application not initialized")
    }
    
    init {
        _instance = this
    }
}