import argparse
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

from functions.call_function import available_functions, call_function
from prompts import system_prompt

load_dotenv()
api_key = os.environ.get("OPENROUTER_API_KEY")
print("API Key Prefix:", api_key[:7] if api_key else "None")
if api_key is None:
    raise RuntimeError("key is not found")

parser = argparse.ArgumentParser(description="Chatbot")
parser.add_argument("user_prompt", type=str, help="User prompt")
parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
args = parser.parse_args()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": args.user_prompt},
]

if args.verbose:
    print(f"User prompt: {args.user_prompt}")

for _ in range(20):
    time.sleep(2)
    response = client.chat.completions.create(
        model ="apodex/apodex-1.1-mini:free",
        messages=messages,
        tools=available_functions,
        temperature=0,
    )

    if response.usage is None:
        raise RuntimeError("Usage data is missing from the response")

    if args.verbose:
        print(f"Prompt tokens: {response.usage.prompt_tokens}")
        print(f"Response tokens: {response.usage.completion_tokens}")

    message = response.choices[0].message
    messages.append(message)

    if not message.tool_calls:
        print(message.content)
        break

    for tool_call in message.tool_calls:
        result_message = call_function(tool_call, verbose=args.verbose)

        if not result_message.get("content"):
            raise RuntimeError("Tool result content cannot be empty")

        if args.verbose:
            print(f"-> {result_message['content']}")

        messages.append(result_message)
else:
    print("Error: Maximum number of iterations reached without a final response")
    raise SystemExit(1)