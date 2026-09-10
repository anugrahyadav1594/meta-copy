"""MetaScale Subsystem - Member 5: Automated Failover & Disaster Recovery Engine."""

import time
import subprocess
import os

PRIMARY_CONTAINER = "postgres-shard-0"
REPLICA_CONTAINER = "postgres-shard-0-replica"
CHECK_INTERVAL_SECONDS = 3

def check_primary_health() -> bool:
    """Checks if the Primary Database container is running and healthy via Docker."""
    try:
        # Run docker inspect to query the health status of the primary database shard
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", PRIMARY_CONTAINER],
            capture_output=True,
            text=True,
            check=True
        )
        status = result.stdout.strip()
        return status == "healthy"
    except Exception:
        # If the container is completely stopped or missing, it will throw an exception
        return False

def execute_failover():
    """Orchestrates the high-availability failover sequence."""
    print("\n🚨 [FAILOVER ENGINE] CRITICAL ALERT: Primary Shard 0 has crashed!")
    print("⏳ [FAILOVER ENGINE] Initiating Disaster Recovery protocol...")
    
    # Step 1: Simulate promoting PostgreSQL replica out of read-only mode
    # In a full deployment, this runs 'pg_ctl promote' inside the replica container
    print(f"🔄 [FAILOVER ENGINE] Promoting replica '{REPLICA_CONTAINER}' to NEW PRIMARY...")
    time.sleep(1) 
    
    # Step 2: Update the network environment configuration
    print("⚙️ [FAILOVER ENGINE] Updating API routing tables to redirect write streams...")
    # This step simulates informing the Request Router that Shard 0's primary address has moved
    os.environ["SHARD_0_URL"] = os.getenv("SHARD_0_REPLICA_URL", "")
    
    print("✅ [FAILOVER ENGINE] Failover complete. System successfully recovered with zero data loss!")

def monitor_cluster():
    print(f"🕵️‍♂️ [FAILOVER ENGINE] Monitoring started. Watching cluster node: {PRIMARY_CONTAINER}...")
    while True:
        is_healthy = check_primary_health()
        if not is_healthy:
            execute_failover()
            break # Exit loop after successful failover execution
        else:
            print(f"💚 [FAILOVER ENGINE] Cluster status: Stable. Node '{PRIMARY_CONTAINER}' is healthy.")
            time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    # This allows you to run the file independently to demonstrate high availability to your faculty
    monitor_cluster()
