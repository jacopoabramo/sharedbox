"""
Example demonstrating SharedDict initialization with user-provided data.

This example shows how to initialize a SharedDict with a pre-existing
dictionary, making it easy to populate shared memory with initial data.
"""

import multiprocessing as mp
import time

import numpy as np

from sharedbox import SharedDict


def demonstrate_basic_initialization():
    """Demonstrate basic initialization with various data types."""

    print("=== Basic SharedDict Initialization ===\n")

    # Prepare initial data (flat structure - no nested dicts)
    initial_data = {
        "config_name": "MyApplication",
        "version": "2.1.0",
        "max_connections": 1000,
        "timeout_seconds": 30.0,
        "debug_enabled": True,
        "supported_formats": ["json", "xml", "csv"],
        "server_host": "localhost",
        "server_port": 8080,
        "server_ssl": False,
        "feature_weights": np.array([0.1, 0.3, 0.6], dtype=np.float32),
    }

    print("1. Creating SharedDict with initial data...")
    print(f"   Initial data has {len(initial_data)} keys:")
    for key, value in initial_data.items():
        if isinstance(value, np.ndarray):
            print(f"   - {key}: numpy array shape {value.shape}, dtype {value.dtype}")
        elif isinstance(value, (list, dict)):
            print(f"   - {key}: {type(value).__name__} with {len(value)} items")
        else:
            print(f"   - {key}: {value} ({type(value).__name__})")

    # Create SharedDict with initial data
    shared_dict = SharedDict("demo_config", initial_data, create=True)

    print("\n2. SharedDict created successfully!")
    print(f"   - Size: {len(shared_dict)} items")
    print(f"   - All keys: {list(shared_dict.keys())}")

    # Verify the data
    print("\n3. Verifying data integrity...")
    print(f"   - Config name: {shared_dict['config_name']}")
    print(f"   - Max connections: {shared_dict['max_connections']}")
    print(f"   - Feature weights: {shared_dict['feature_weights']}")
    print(f"   - Server host: {shared_dict['server_host']}")
    print(f"   - Server port: {shared_dict['server_port']}")

    # Demonstrate that the SharedDict still works normally
    print("\n4. Testing normal SharedDict operations...")

    # Add new data
    shared_dict["startup_time"] = time.time()
    shared_dict["active_users"] = 0

    # Modify existing data
    shared_dict["max_connections"] = 2000

    # Check updates
    print(f"   - Updated max_connections: {shared_dict['max_connections']}")
    print(f"   - Added startup_time: {shared_dict['startup_time']}")
    print(f"   - Total keys now: {len(shared_dict)}")

    # Clean up
    shared_dict.close()
    shared_dict.unlink()
    print("\n5. Cleanup complete!\n")


def worker_process(shared_name: str, worker_id: int):
    """Worker process that uses pre-initialized SharedDict."""

    # Connect to existing SharedDict (not creating, just connecting)
    shared_dict = SharedDict(shared_name, create=False)

    print(f"Worker {worker_id}: Connected to SharedDict")
    print(f"Worker {worker_id}: Found {len(shared_dict)} pre-initialized items")

    # Read initial configuration
    app_name = shared_dict.get("app_name", "Unknown")
    worker_limit = shared_dict.get("max_workers", 4)

    print(f"Worker {worker_id}: App = {app_name}, Worker limit = {worker_limit}")

    # Add worker-specific data
    shared_dict[f"worker_{worker_id}_status"] = "active"
    shared_dict[f"worker_{worker_id}_tasks"] = 0

    # Simulate some work
    for i in range(5):
        time.sleep(0.1)
        shared_dict[f"worker_{worker_id}_tasks"] = i + 1

    shared_dict[f"worker_{worker_id}_status"] = "completed"
    print(f"Worker {worker_id}: Work completed")
    shared_dict.close()


def demonstrate_multiprocess_with_initialization():
    """Demonstrate multiprocessing with pre-initialized data."""

    print("=== Multiprocess SharedDict with Initialization ===\n")

    # Prepare application configuration (flat structure)
    app_config = {
        "app_name": "DataProcessor",
        "version": "1.0",
        "max_workers": 4,
        "batch_size": 100,
        "processing_algorithm": "advanced",
        "processing_threshold": 0.95,
        "feature_matrix": np.random.randn(10, 50).astype(np.float32),
        "class_labels": ["A", "B", "C", "D", "E"],
    }

    shared_name = "multiprocess_demo"

    print("1. Creating SharedDict with application configuration...")

    # Initialize SharedDict with configuration
    shared_dict = SharedDict(
        shared_name,
        app_config,
        create=True,
        size=10 * 1024 * 1024,  # 10MB should be plenty
    )

    print(f"   - Initialized with {len(app_config)} configuration items")
    print(f"   - Feature matrix shape: {shared_dict['feature_matrix'].shape}")

    # Start worker processes
    print(f"\n2. Starting {app_config['max_workers']} worker processes...")

    processes = []
    for i in range(app_config["max_workers"]):
        p = mp.Process(target=worker_process, args=(shared_name, i))
        processes.append(p)
        p.start()

    # Wait for all processes to complete
    for p in processes:
        p.join()

    print("\n3. All workers completed. Final SharedDict state:")
    print(f"   - Total items: {len(shared_dict)}")

    # Show worker results
    for key in sorted(shared_dict.keys()):
        if key.startswith("worker_"):
            print(f"   - {key}: {shared_dict[key]}")

    # Clean up
    shared_dict.close()
    shared_dict.unlink()
    print("\n4. Cleanup complete!\n")


def demonstrate_ml_model_initialization():
    """Demonstrate ML model initialization with pre-trained data."""

    print("=== ML Model SharedDict Initialization ===\n")

    # Simulate a trained ML model (flat structure)
    model_data = {
        "model_id": "classifier_v2",
        "model_type": "RandomForest",
        "training_date": "2024-01-15",
        "accuracy": 0.94,
        "feature_names": [
            "age",
            "income",
            "education_years",
            "location_score",
            "credit_score",
            "employment_years",
            "debt_ratio",
        ],
        "class_names": ["low_risk", "medium_risk", "high_risk"],
        "feature_weights": np.random.randn(7).astype(np.float32),
        # Flattened model parameters
        "n_estimators": 100,
        "max_depth": 10,
        "min_samples_split": 5,
        # Flattened preprocessing stats
        "feature_means": np.array([35.2, 65000, 12.5, 0.7, 720, 8.2, 0.3]),
        "feature_stds": np.array([12.1, 25000, 3.1, 0.2, 85, 4.1, 0.15]),
        # Flattened decision tree info (use separate arrays)
        "tree_depths": [8, 9, 7],
        "tree_nodes": [127, 203, 89],
    }

    print("1. Initializing SharedDict with trained ML model...")
    print(f"   - Model ID: {model_data['model_id']}")
    print(f"   - Accuracy: {model_data['accuracy']:.2%}")
    print(f"   - Features: {len(model_data['feature_names'])}")
    print(f"   - Classes: {len(model_data['class_names'])}")

    model_dict = SharedDict("ml_model_demo", model_data, create=True)

    print(f"\n2. Model loaded into SharedDict ({len(model_dict)} items)")

    # Simulate prediction requests (store as separate values, not nested dicts)
    test_ages = [30, 45, 25]
    test_incomes = [55000, 85000, 35000]
    test_scores = [680, 750, 600]

    print(f"\n3. Running predictions on {len(test_ages)} samples...")

    for i in range(len(test_ages)):
        # Simple mock prediction using feature weights
        features = np.array(
            [test_ages[i], test_incomes[i], 12, 0.5, test_scores[i], 5, 0.2]
        )

        weights = model_dict["feature_weights"]
        score = np.dot(features, weights)

        # Convert score to risk category
        if score > 0.5:
            risk = "low_risk"
        elif score > -0.5:
            risk = "medium_risk"
        else:
            risk = "high_risk"

        sample_info = (
            f"age={test_ages[i]}, income={test_incomes[i]}, credit={test_scores[i]}"
        )
        print(f"   Sample {i + 1}: {sample_info} → {risk} (score: {score:.2f})")

        # Store prediction results (flattened, no nested dicts)
        model_dict[f"prediction_{i + 1}_score"] = float(score)
        model_dict[f"prediction_{i + 1}_risk"] = risk
        model_dict[f"prediction_{i + 1}_age"] = test_ages[i]
        model_dict[f"prediction_{i + 1}_income"] = test_incomes[i]

    print(f"\n4. Predictions complete. SharedDict now has {len(model_dict)} items")

    # Show some stored predictions
    for i in range(1, len(test_ages) + 1):
        risk_key = f"prediction_{i}_risk"
        score_key = f"prediction_{i}_score"
        if risk_key in model_dict and score_key in model_dict:
            risk = model_dict[risk_key]
            score = model_dict[score_key]
            print(f"   prediction_{i}: {risk} (score: {score:.2f})")

    model_dict.close()
    model_dict.unlink()
    print("\n5. Model cleanup complete!\n")


def performance_comparison():
    """Compare initialization performance: manual vs. bulk initialization."""

    print("=== Performance Comparison ===\n")

    # Generate test data (flat structure)
    test_data = {}
    for i in range(1000):
        test_data[f"item_{i}_id"] = i
        test_data[f"item_{i}_name"] = f"item_{i}"
        test_data[f"item_{i}_values"] = np.random.randn(50)
        test_data[f"item_{i}_type"] = "test"
        test_data[f"item_{i}_batch"] = i // 100

    print(
        f"Testing with {len(test_data)} items ({len(test_data) // 5} logical items)..."
    )

    # Method 1: Manual initialization
    print("\n1. Manual initialization (item by item)...")
    start_time = time.time()

    manual_dict = SharedDict("manual_init", create=True, size=50 * 1024 * 1024)
    for key, value in test_data.items():
        manual_dict[key] = value

    manual_time = time.time() - start_time
    print(f"   Time: {manual_time:.3f} seconds")

    # Method 2: Bulk initialization
    print("\n2. Bulk initialization (constructor data parameter)...")
    start_time = time.time()

    bulk_dict = SharedDict("bulk_init", test_data, create=True, size=50 * 1024 * 1024)

    bulk_time = time.time() - start_time
    print(f"   Time: {bulk_time:.3f} seconds")

    # Verify both have the same data
    assert len(manual_dict) == len(bulk_dict) == len(test_data)

    # Check a few items for equality
    for i in [0, 100, 500]:
        values_key = f"item_{i}_values"
        batch_key = f"item_{i}_batch"
        assert np.array_equal(manual_dict[values_key], bulk_dict[values_key])
        assert manual_dict[batch_key] == bulk_dict[batch_key]

    print("\n3. Performance comparison:")
    print(f"   Manual: {manual_time:.3f}s")
    print(f"   Bulk:   {bulk_time:.3f}s")

    if bulk_time < manual_time:
        speedup = manual_time / bulk_time
        print(f"   Bulk initialization is {speedup:.1f}x faster!")
    else:
        print("   Performance similar (timing may vary)")

    # Clean up
    manual_dict.close()
    bulk_dict.close()
    manual_dict.unlink()
    bulk_dict.unlink()
    print("\n4. Performance test cleanup complete!\n")


def main():
    """Run all demonstrations."""

    # Set multiprocessing method for cross-platform compatibility
    mp.set_start_method("spawn", force=True)

    try:
        print("SharedDict Data Initialization Examples")
        print("=" * 50)

        demonstrate_basic_initialization()
        demonstrate_multiprocess_with_initialization()
        demonstrate_ml_model_initialization()
        performance_comparison()

        print("🎉 All demonstrations completed successfully!")

    except Exception as e:
        print(f"❌ Error during demonstration: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
