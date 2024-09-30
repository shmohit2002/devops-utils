import time
import requests
import threading

# Configuration
url = "sampledomain.com/endpoint"
payload = {"data":"sampledata"}
headers = {"Content-Type": "application/json"}

num_requests = 20  # Number of requests to send
concurrent_threads = 5  # Number of concurrent threads

results = {
    'success': 0,
    'failure': 0,
}
times= []

def send_request():
    global results
    start_time = time.time()
    try:
        response = requests.post(url, json=payload, headers=headers)
        end_time = time.time()
        elapsed_time = end_time - start_time
        times.append(elapsed_time)
        if response.status_code == 200:
            results['success'] += 1
        else:
            results['failure'] += 1
    except requests.exceptions.RequestException as e:
        results['failure'] += 1

def stress_test():
    threads = []
    for _ in range(num_requests):
        if len(threads) >= concurrent_threads:
            for thread in threads:
                thread.join()
            threads = []
        thread = threading.Thread(target=send_request)
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

if __name__ == "__main__":
    stress_test()
    avg_time = sum(times) / len(times) if times else 0
    print(f"Stress Test Results: {results}")
    print(f"Average Time per Request: {avg_time} seconds")
    print(f"Top 5 longest request times (in seconds): {sorted(times, reverse=True)[:5]}")