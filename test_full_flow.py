import sys
sys.stdout.reconfigure(encoding='utf-8')

# Test the full jarvis startup and one command
import jarvis
import threading
import time

# Start jarvis in background
def run_jarvis():
    jarvis.startup()

jarvis_thread = threading.Thread(target=run_jarvis, daemon=True)
jarvis_thread.start()

# Wait for startup
time.sleep(3)

# Simulate a command by calling the planner and executor directly
from jarvis import planner, execute_with_feedback

# Test declarative fact
command = 'My temporary test word is cherry.'
action, spoken_response, _model_type = planner.ask(command)
print(f'Planner action: {action}')
print(f'Planner spoken_response: "{spoken_response}"')

if action:
    success, exec_message = execute_with_feedback(action, original_input=command)
    print(f'Execute success: {success}')
    print(f'Execute message: {exec_message}')
    
    # Simulate what jarvis.py does
    final_response = exec_message if exec_message else (spoken_response if success else "Something went wrong.")
    print(f'Final response: {final_response}')
    
    # Test fact query
    command2 = 'What is my temporary test word?'
    action2, spoken_response2, _model_type = planner.ask(command2)
    print(f'Query action: {action2}')
    print(f'Query spoken_response: "{spoken_response2}"')
    
    if action2:
        success2, exec_message2 = execute_with_feedback(action2, original_input=command2)
        print(f'Query success: {success2}')
        print(f'Query message: {exec_message2[:200]}...')