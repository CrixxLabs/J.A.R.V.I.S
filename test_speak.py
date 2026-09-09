import sys
sys.stdout.reconfigure(encoding='utf-8')

# Test the speak function
from jarvis import speak, jarvisify_response

# Test jarvisify_response
result = jarvisify_response("Got it — saved: 'Test word is banana.'.")
print(f'jarvisify_response: "{result}"')

# Test speak
speak("Test message from speak function")
print("Speak completed")