import urllib.request

try:
    print("Triggering patch-db-schema...")
    with urllib.request.urlopen("http://127.0.0.1:5000/patch-db-schema") as response:
        html = response.read().decode('utf-8')
        print("Response:", html)
except Exception as e:
    print(f"Error: {e}")
