package com.lilly.assistant.core

import android.content.Context
import android.content.pm.PackageManager
import com.lilly.assistant.database.SkillDao
import com.lilly.assistant.models.InstalledSkill
import kotlinx.coroutines.*

class SkillManager(
    private val context: Context,
    private val skillDao: SkillDao
) {
    
    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    
    suspend fun initialize() {
        withContext(Dispatchers.Default) {
            // Scan for installed skills on startup
            scanInstalledSkills()
        }
    }
    
    suspend fun scanInstalledSkills() {
        withContext(Dispatchers.Default) {
            try {
                val packageManager = context.packageManager
                val installedPackages = packageManager.getInstalledPackages(PackageManager.GET_META_DATA)
                
                val skillPackages = installedPackages.filter { pkg ->
                    // Check if package has Lilly skill metadata
                    try {
                        val appInfo = packageManager.getApplicationInfo(pkg.packageName, PackageManager.GET_META_DATA)
                        appInfo.metaData?.containsKey("lilly_skill") == true
                    } catch (e: Exception) {
                        false
                    }
                }
                
                for (pkg in skillPackages) {
                    val appInfo = packageManager.getApplicationInfo(pkg.packageName, PackageManager.GET_META_DATA)
                    val skillMetadata = appInfo.metaData
                    
                    val skill = InstalledSkill(
                        id = pkg.packageName.hashCode().toString(),
                        name = packageManager.getApplicationLabel(pkg).toString(),
                        packageName = pkg.packageName,
                        description = skillMetadata?.getString("lilly_skill_description") ?: "No description",
                        version = pkg.versionName ?: "1.0",
                        isEnabled = true,
                        installedAt = System.currentTimeMillis()
                    )
                    
                    // Check if skill already exists
                    val existingSkill = skillDao.getInstalledSkillByPackage(pkg.packageName)
                    if (existingSkill == null) {
                        skillDao.insertInstalledSkill(skill)
                    } else {
                        // Update if version changed
                        if (existingSkill.version != skill.version) {
                            skillDao.updateInstalledSkill(skill.copy(id = existingSkill.id))
                        }
                    }
                }
            } catch (e: Exception) {
                // Handle scanning error
            }
        }
    }
    
    suspend fun getActiveSkillsCount(): Int {
        return withContext(Dispatchers.Default) {
            try {
                skillDao.getInstalledSkills().count { it.isEnabled }
            } catch (e: Exception) {
                0
            }
        }
    }
    
    suspend fun enableSkill(skillId: String, enabled: Boolean) {
        withContext(Dispatchers.Default) {
            try {
                val skill = skillDao.getInstalledSkillById(skillId)
                skill?.let {
                    skillDao.updateInstalledSkill(it.copy(isEnabled = enabled))
                }
            } catch (e: Exception) {
                // Handle error
            }
        }
    }
    
    suspend fun getSkillByPackage(packageName: String): InstalledSkill? {
        return withContext(Dispatchers.Default) {
            try {
                skillDao.getInstalledSkillByPackage(packageName)
            } catch (e: Exception) {
                null
            }
        }
    }
    
    fun shutdown() {
        scope.cancel()
    }
}