import sys
sys.stdout.reconfigure(encoding='utf-8')

from jarvis import execute_with_feedback, planner

# Test fact_query flow
command = 'What is my temporary test word?'
action, spoken_response, _model_type = planner.ask(command)
print(f'Planner action: {action}')
print(f'Planner spoken_response: "{spoken_response}"')

if action:
    success, exec_message = execute_with_feedback(action, original_input=command)
    print(f'Execute success: {success}')
    print(f'Execute message length: {len(exec_message) if exec_message else 0}')
    if exec_message:
        print(f'Execute message preview: {exec_message[:200]}...')