package com.lilly.assistant.database

import androidx.room.*
import com.lilly.assistant.models.Memory
import kotlinx.coroutines.flow.Flow

@Dao
interface MemoryDao {
    
    @Query("SELECT * FROM memories ORDER BY timestamp DESC")
    suspend fun getAllMemories(): List<Memory>
    
    @Query("SELECT * FROM memories ORDER BY timestamp DESC")
    fun getAllMemoriesFlow(): Flow<List<Memory>>
    
    @Query("SELECT * FROM memories WHERE id = :id")
    suspend fun getMemoryById(id: String): Memory?
    
    @Query("SELECT * FROM memories WHERE key LIKE '%' || :query || '%' OR value LIKE '%' || :query || '%'")
    suspend fun searchMemories(query: String): List<Memory>
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertMemory(memory: Memory)
    
    @Update
    suspend fun updateMemory(memory: Memory)
    
    @Delete
    suspend fun deleteMemory(memory: Memory)
    
    @Query("DELETE FROM memories")
    suspend fun clearAllMemories()
    
    @Query("SELECT COUNT(*) FROM memories")
    suspend fun getMemoryCount(): Int
}