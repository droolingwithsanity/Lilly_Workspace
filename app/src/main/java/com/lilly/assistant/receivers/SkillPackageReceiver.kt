package com.lilly.assistant.receivers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.lilly.assistant.LillyApplication
import com.lilly.assistant.services.LillyForegroundService
import kotlinx.coroutines.*

class SkillPackageReceiver : BroadcastReceiver() {
    
    override fun onReceive(context: Context?, intent: Intent?) {
        context ?: return
        intent ?: return
        
        when (intent.action) {
            Intent.ACTION_PACKAGE_ADDED -> handlePackageAdded(context, intent)
            Intent.ACTION_PACKAGE_REMOVED -> handlePackageRemoved(context, intent)
            Intent.ACTION_PACKAGE_REPLACED -> handlePackageReplaced(context, intent)
            Intent.ACTION_PACKAGE_CHANGED -> handlePackageChanged(context, intent)
            ACTION_SKILL_INSTALLED -> handleSkillInstalled(context, intent)
            ACTION_SKILL_ACTIVATED -> handleSkillActivated(context, intent)
        }
    }
    
    // Handle package added
    private fun handlePackageAdded(context: Context, intent: Intent) {
        val packageName = intent.data?.schemeSpecificPart ?: return
        
        // Check if this is a Lilly skill
        if (isLillySkill(context, packageName)) {
            installSkill(context, packageName)
        }
    }
    
    // Handle package removed
    private fun handlePackageRemoved(context: Context, intent: Intent) {
        val packageName = intent.data?.schemeSpecificPart ?: return
        
        // Remove skill if it was installed
        removeSkill(context, packageName)
    }
    
    // Handle package replaced
    private fun handlePackageReplaced(context: Context, intent: Intent) {
        val packageName = intent.data?.schemeSpecificPart ?: return
        
        // Update skill
        updateSkill(context, packageName)
    }
    
    // Handle package changed
    private fun handlePackageChanged(context: Context, intent: Intent) {
        val packageName = intent.data?.schemeSpecificPart ?: return
        
        // Update skill state
        updateSkillState(context, packageName)
    }
    
    // Handle skill installed broadcast
    private fun handleSkillInstalled(context: Context, intent: Intent) {
        val packageName = intent.getStringExtra(EXTRA_PACKAGE_NAME) ?: return
        installSkill(context, packageName)
    }
    
    // Handle skill activated broadcast
    private fun handleSkillActivated(context: Context, intent: Intent) {
        val packageName = intent.getStringExtra(EXTRA_PACKAGE_NAME) ?: return
        activateSkill(context, packageName)
    }
    
    // Check if package is a Lilly skill
    private fun isLillySkill(context: Context, packageName: String): Boolean {
        return try {
            val packageInfo = context.packageManager.getPackageInfo(packageName, android.content.pm.PackageManager.GET_META_DATA)
            val metaData = packageInfo.applicationInfo?.metaData
            
            metaData?.getBoolean("is_lilly_skill", false) ?: false
        } catch (e: Exception) {
            false
        }
    }
    
    // Install skill
    private fun installSkill(context: Context, packageName: String) {
        val app = context.applicationContext as? LillyApplication ?: return
        
        CoroutineScope(Dispatchers.IO).launch {
            val success = app.skillManager.installSkill(packageName)
            
            if (success) {
                // Send notification
                sendNotification(context, "Skill Installed", "New skill has been installed: $packageName")
                
                // Start service if not running
                LillyForegroundService.startService(context)
            }
        }
    }
    
    // Remove skill
    private fun removeSkill(context: Context, packageName: String) {
        val app = context.applicationContext as? LillyApplication ?: return
        
        CoroutineScope(Dispatchers.IO).launch {
            // Deactivate skill first
            app.skillManager.deactivateSkill(packageName)
            
            // Send notification
            sendNotification(context, "Skill Removed", "Skill has been removed: $packageName")
        }
    }
    
    // Update skill
    private fun updateSkill(context: Context, packageName: String) {
        val app = context.applicationContext as? LillyApplication ?: return
        
        CoroutineScope(Dispatchers.IO).launch {
            // Reinstall skill
            app.skillManager.installSkill(packageName)
            
            // Send notification
            sendNotification(context, "Skill Updated", "Skill has been updated: $packageName")
        }
    }
    
    // Update skill state
    private fun updateSkillState(context: Context, packageName: String) {
        val app = context.applicationContext as? LillyApplication ?: return
        
        CoroutineScope(Dispatchers.IO).launch {
            // Check if skill is still active
            val activeSkills = app.skillManager.getActiveSkills()
            val isActive = activeSkills.any { it.packageName == packageName }
            
            if (!isActive) {
                // Deactivate skill
                app.skillManager.deactivateSkill(packageName)
            }
        }
    }
    
    // Activate skill
    private fun activateSkill(context: Context, packageName: String) {
        val app = context.applicationContext as? LillyApplication ?: return
        
        CoroutineScope(Dispatchers.IO).launch {
            val success = app.skillManager.activateSkill(packageName)
            
            if (success) {
                // Send notification
                sendNotification(context, "Skill Activated", "Skill has been activated: $packageName")
                
                // Update system prompts
                app.systemPromptManager.setActiveSkills(
                    app.skillManager.getActiveSkills().map { it.packageName }
                )
            }
        }
    }
    
    // Send notification
    private fun sendNotification(context: Context, title: String, message: String) {
        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
        
        val notification = android.app.Notification.Builder(context, "lilly_skills_channel")
            .setContentTitle(title)
            .setContentText(message)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .build()
        
        notificationManager.notify(System.currentTimeMillis().toInt(), notification)
    }
    
    companion object {
        const val ACTION_SKILL_INSTALLED = "com.lilly.assistant.SKILL_INSTALLED"
        const val ACTION_SKILL_ACTIVATED = "com.lilly.assistant.SKILL_ACTIVATED"
        const val EXTRA_PACKAGE_NAME = "package_name"
    }
}