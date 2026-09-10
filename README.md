# MetaScale Asynchronous Denormalization Worker

An event-driven, asynchronous denormalization pipeline designed to maintain read-optimized PostgreSQL tables for high-throughput social media interactions (such as post likes) without blocking canonical write paths.

---

## Component Architecture

* **Message Broker**: RabbitMQ (`like_events_queue`) handles incoming asynchronous interaction events.
* **Consumer Worker**: Java 21 & Spring Boot 3.2 application (`java-worker`) running a Spring AMQP listener (`@RabbitListener`).
* **Read Model**: PostgreSQL `denormalized_post_feed` table optimized for low-latency read queries.

---

## Directory Structure

```text
denormalization/
├── 004_denormalized_read_model.sql       # Database schema migration for read-optimized table
└── java-worker/                          # Spring Boot consumer application
    ├── pom.xml                           # Maven build and dependency configuration
    └── src/
        └── main/
            ├── java/com/metascale/worker/
            │   └── WorkerApplication.java # Spring Boot entry point & RabbitMQ listener service
            └── resources/
                └── application.properties # Database and RabbitMQ connection properties
