import sys
sys.stdout.reconfigure(encoding='utf-8')

from jarvis import execute_with_feedback, planner

# Test the flow
command = 'My temporary test word is banana.'
action, spoken_response, _model_type = planner.ask(command)
print(f'Planner action: {action}')
print(f'Planner spoken_response: "{spoken_response}"')

if action:
    success, exec_message = execute_with_feedback(action, original_input=command)
    print(f'Execute success: {success}')
    print(f'Execute message: {exec_message}')