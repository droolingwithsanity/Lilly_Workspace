package com.lilly.assistant.core

import android.content.Context
import android.content.pm.PackageManager
import com.lilly.assistant.database.SkillDao
import com.lilly.assistant.models.InstalledSkill
import kotlinx.coroutines.*

class LillyPackageManager(
    private val context: Context,
    private val skillDao: SkillDao
) {
    
    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    
    suspend fun initialize() {
        withContext(Dispatchers.Default) {
            // Initialize package manager
        }
    }
    
    suspend fun getInstalledSkills(): List<InstalledSkill> {
        return withContext(Dispatchers.Default) {
            try {
                skillDao.getInstalledSkills()
            } catch (e: Exception) {
                emptyList()
            }
        }
    }
    
    suspend fun getInstalledSkillById(id: String): InstalledSkill? {
        return withContext(Dispatchers.Default) {
            try {
                skillDao.getInstalledSkillById(id)
            } catch (e: Exception) {
                null
            }
        }
    }
    
    suspend fun installSkill(skill: InstalledSkill): Boolean {
        return withContext(Dispatchers.Default) {
            try {
                // Check if skill is already installed
                val existing = skillDao.getInstalledSkillByPackage(skill.packageName)
                if (existing != null) {
                    return@withContext false
                }
                
                // Install the skill
                skillDao.insertInstalledSkill(skill)
                true
            } catch (e: Exception) {
                false
            }
        }
    }
    
    suspend fun uninstallSkill(skillId: String): Boolean {
        return withContext(Dispatchers.Default) {
            try {
                val skill = skillDao.getInstalledSkillById(skillId)
                skill?.let {
                    skillDao.deleteInstalledSkill(it)
                    true
                } ?: false
            } catch (e: Exception) {
                false
            }
        }
    }
    
    suspend fun updateSkill(skill: InstalledSkill): Boolean {
        return withContext(Dispatchers.Default) {
            try {
                skillDao.updateInstalledSkill(skill)
                true
            } catch (e: Exception) {
                false
            }
        }
    }
    
    suspend fun searchSkills(query: String): List<InstalledSkill> {
        return withContext(Dispatchers.Default) {
            try {
                skillDao.getInstalledSkills().filter { skill ->
                    skill.name.contains(query, ignoreCase = true) ||
                    skill.description.contains(query, ignoreCase = true) ||
                    skill.packageName.contains(query, ignoreCase = true)
                }
            } catch (e: Exception) {
                emptyList()
            }
        }
    }
    
    suspend fun isSkillInstalled(packageName: String): Boolean {
        return withContext(Dispatchers.Default) {
            try {
                skillDao.getInstalledSkillByPackage(packageName) != null
            } catch (e: Exception) {
                false
            }
        }
    }
    
    fun shutdown() {
        scope.cancel()
    }
}