package com.lilly.assistant.database

import androidx.room.*
import com.lilly.assistant.models.Preference
import kotlinx.coroutines.flow.Flow

@Dao
interface PreferenceDao {
    
    @Query("SELECT * FROM preferences ORDER BY key ASC")
    suspend fun getAllPreferences(): List<Preference>
    
    @Query("SELECT * FROM preferences ORDER BY key ASC")
    fun getAllPreferencesFlow(): Flow<List<Preference>>
    
    @Query("SELECT * FROM preferences WHERE key = :key")
    suspend fun getPreferenceByKey(key: String): Preference?
    
    @Query("SELECT value FROM preferences WHERE key = :key")
    suspend fun getPreferenceValue(key: String): String?
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertPreference(preference: Preference)
    
    @Update
    suspend fun updatePreference(preference: Preference)
    
    @Delete
    suspend fun deletePreference(preference: Preference)
    
    @Query("DELETE FROM preferences WHERE key = :key")
    suspend fun deletePreferenceByKey(key: String)
    
    @Query("DELETE FROM preferences")
    suspend fun deleteAllPreferences()
}