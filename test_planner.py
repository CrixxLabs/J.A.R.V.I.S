import sys
sys.stdout.reconfigure(encoding='utf-8')

from planner import ask

# Test the planner's ask function directly
action, spoken_response, model_type = ask('My temporary test word is banana.')
print(f'Action: {action}')
print(f'Spoken response: "{spoken_response}"')
print(f'Model type: {model_type}')