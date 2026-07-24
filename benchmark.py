import time
import os
import sys

# Add the directory to sys.path so we can import cli
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from cli import PROVIDERS, get_provider, load_config, SecureStorage, SECRETS_PATH

def run_benchmark():
    config = load_config()
    secrets = SecureStorage(SECRETS_PATH)
    
    messages = [{"role": "user", "content": "Write a python function to compute the fibonacci sequence. Give ONLY the python code without any markdown or explanation."}]
    
    print("=" * 60)
    print("NewMeta Agents Benchmark")
    print("=" * 60)
    
    results = []
    
    for provider_name in PROVIDERS.keys():
        # Only benchmark free or locally available ones to avoid burning user's paid tokens unless they are set
        if provider_name not in ['dsfree', 'kimifree', 'mistral', 'groq', 'mephissa', 'ollama']:
            continue
            
        print(f"Benchmarking {provider_name}...")
        try:
            provider = get_provider(provider_name, config, secrets)
            # Skip if no models
            models = provider.models()
            if not models:
                print(f"  [Skipped] No models available.")
                continue
            
            start_time = time.time()
            # We'll just collect the first chunk to measure Time-To-First-Token (TTFT)
            # and then the full text to measure total time.
            ttft = None
            total_chars = 0
            
            response_generator = provider.chat(messages, stream=True)
            for chunk in response_generator:
                if ttft is None:
                    ttft = time.time() - start_time
                if isinstance(chunk, str):
                    total_chars += len(chunk)
                elif hasattr(chunk, 'choices') and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, 'content') and delta.content:
                        total_chars += len(delta.content)
            
            total_time = time.time() - start_time
            if total_chars == 0:
                print(f"  [Failed] Empty response.")
                continue
                
            chars_per_sec = total_chars / total_time
            print(f"  TTFT: {ttft:.2f}s | Total Time: {total_time:.2f}s | Speed: {chars_per_sec:.0f} chars/sec")
            results.append({
                "provider": provider_name,
                "ttft": ttft,
                "total_time": total_time,
                "speed": chars_per_sec
            })
            
        except Exception as e:
            print(f"  [Error] {str(e)[:100]}")
            
    print("\n" + "=" * 60)
    print("BENCHMARK RESULTS (Sorted by Speed)")
    print("=" * 60)
    results.sort(key=lambda x: x["speed"], reverse=True)
    for i, r in enumerate(results):
        print(f"{i+1}. {r['provider'].ljust(15)} | Speed: {r['speed']:>4.0f} c/s | TTFT: {r['ttft']:>4.2f}s")
    print("=" * 60)

if __name__ == "__main__":
    run_benchmark()
