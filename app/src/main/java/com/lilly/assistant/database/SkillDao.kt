package com.lilly.assistant.database

import androidx.room.*
import com.lilly.assistant.models.InstalledSkill
import kotlinx.coroutines.flow.Flow

@Dao
interface SkillDao {
    
    @Query("SELECT * FROM installed_skills ORDER BY name ASC")
    suspend fun getInstalledSkills(): List<InstalledSkill>
    
    @Query("SELECT * FROM installed_skills ORDER BY name ASC")
    fun getInstalledSkillsFlow(): Flow<List<InstalledSkill>>
    
    @Query("SELECT * FROM installed_skills WHERE isEnabled = 1 ORDER BY name ASC")
    suspend fun getEnabledSkills(): List<InstalledSkill>
    
    @Query("SELECT * FROM installed_skills WHERE id = :id")
    suspend fun getInstalledSkillById(id: String): InstalledSkill?
    
    @Query("SELECT * FROM installed_skills WHERE packageName = :packageName")
    suspend fun getInstalledSkillByPackage(packageName: String): InstalledSkill?
    
    @Query("SELECT * FROM installed_skills WHERE name LIKE '%' || :query || '%' OR description LIKE '%' || :query || '%'")
    suspend fun searchSkills(query: String): List<InstalledSkill>
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertInstalledSkill(skill: InstalledSkill)
    
    @Update
    suspend fun updateInstalledSkill(skill: InstalledSkill)
    
    @Delete
    suspend fun deleteInstalledSkill(skill: InstalledSkill)
    
    @Query("DELETE FROM installed_skills")
    suspend fun deleteAllSkills()
    
    @Query("SELECT COUNT(*) FROM installed_skills")
    suspend fun getInstalledSkillCount(): Int
    
    @Query("SELECT COUNT(*) FROM installed_skills WHERE isEnabled = 1")
    suspend fun getEnabledSkillCount(): Int
}