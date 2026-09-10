package com.metascale.worker;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

@SpringBootApplication
public class WorkerApplication {

    public static void main(String[] args) {
        SpringApplication.run(WorkerApplication.class, args);
    }
}

@Service
class DenormalizationListener {

    @Autowired
    private JdbcTemplate jdbcTemplate;
    
    private final ObjectMapper objectMapper = new ObjectMapper();

    // Listens to the queue for asynchronous denormalized updates
    @RabbitListener(queues = "like_events_queue")
    public void handleNewLikeEvent(String message) {
        try {
            System.out.println("Received event: " + message);
            
            // 1. Parse the JSON message to get the post_id
            JsonNode jsonNode = objectMapper.readTree(message);
            long postId = jsonNode.get("post_id").asLong();
            
            // 2. The SQL to update the denormalized feed table
            String sql = "UPDATE denormalized_post_feed SET like_count = like_count + 1 WHERE post_id = ?";
            
            // 3. Execute the update
            int rowsAffected = jdbcTemplate.update(sql, postId);
            
            if (rowsAffected > 0) {
                System.out.println("Successfully incremented like_count for post_id: " + postId);
            } else {
                System.out.println("Warning: post_id " + postId + " not found in denormalized_post_feed.");
            }
            
        } catch (Exception e) {
            System.err.println("Error processing message: " + e.getMessage());
        }
    }
}