package com.lilly.assistant.database

import androidx.room.*
import com.lilly.assistant.models.Conversation
import kotlinx.coroutines.flow.Flow

@Dao
interface ConversationDao {
    
    @Query("SELECT * FROM conversations ORDER BY updatedAt DESC")
    suspend fun getAllConversations(): List<Conversation>
    
    @Query("SELECT * FROM conversations WHERE id = :id")
    suspend fun getConversationById(id: String): Conversation?
    
    @Query("SELECT * FROM conversations WHERE id = :id")
    fun getConversationByIdFlow(id: String): Flow<Conversation?>
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertConversation(conversation: Conversation)
    
    @Update
    suspend fun updateConversation(conversation: Conversation)
    
    @Delete
    suspend fun deleteConversation(conversation: Conversation)
    
    @Query("DELETE FROM conversations")
    suspend fun deleteAllConversations()
    
    @Query("SELECT COUNT(*) FROM conversations")
    suspend fun getConversationCount(): Int
}