from flask import Flask, request, jsonify, Response, stream_with_context, render_template, send_from_directory, redirect
from flask_cors import CORS
import requests
import os
import time
import uuid
import json
from dotenv import load_dotenv
from colorama import Fore, Back, Style, init
from queue import Queue
from threading import Lock
import datetime

# Initialize colorama
init()

# Color constants for logging
REQUEST_COLOR = Fore.CYAN
RESPONSE_COLOR = Fore.GREEN
ERROR_COLOR = Fore.RED
RESET_COLOR = Style.RESET_ALL

# Queue for web logs
log_queue = Queue()
clients = set()
clients_lock = Lock()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
load_dotenv()

# Ollama API endpoint - use host.docker.internal when running in Docker
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://host.docker.internal:11434')
print(f"\n{REQUEST_COLOR}Environment OLLAMA_BASE_URL: {os.getenv('OLLAMA_BASE_URL')}{RESET_COLOR}")
print(f"{REQUEST_COLOR}Using Ollama base URL: {OLLAMA_BASE_URL}{RESET_COLOR}")

# Add cache for model status
model_status_cache = {}

def log_to_web(message, log_type="request"):
    """Send log message to all connected web clients"""
    with clients_lock:
        for client in clients:
            try:
                client.put({
                    "message": message,
                    "type": log_type
                })
            except:
                continue

def proxy_request(method, path, data=None, stream=False):
    url = f"{OLLAMA_BASE_URL}{path}"
    headers = {
        'Content-Type': 'application/json',
    }
    
    # Log the request
    web_url = f"http://localhost:7005/logs"
    request_msg = f"➡️ Sending request to Ollama (View logs at {web_url}):"
    print(f"\n{REQUEST_COLOR}{request_msg}{RESET_COLOR}")
    log_to_web(request_msg)
    
    method_msg = f"Method: {method}"
    print(f"{REQUEST_COLOR}{method_msg}{RESET_COLOR}")
    log_to_web(method_msg)
    
    url_msg = f"URL: {url}"
    print(f"{REQUEST_COLOR}{url_msg}{RESET_COLOR}")
    log_to_web(url_msg)
    
    if data:
        data_msg = f"Data: {json.dumps(data, indent=2)}"
        print(f"{REQUEST_COLOR}{data_msg}{RESET_COLOR}")
        log_to_web(data_msg)
    
    try:
        if stream:
            response = requests.request(
                method,
                url,
                json=data,
                headers=headers,
                stream=True
            )
            response.raise_for_status()
            stream_msg = "⬅️ Received streaming response from Ollama"
            print(f"{RESPONSE_COLOR}{stream_msg}{RESET_COLOR}")
            log_to_web("Stream started", "stream_start")
            return response
        
        response = requests.request(
            method,
            url,
            json=data,
            headers=headers
        )
        response.raise_for_status()
        
        # Log the response
        response_msg = "⬅️ Received response from Ollama:"
        print(f"\n{RESPONSE_COLOR}{response_msg}{RESET_COLOR}")
        log_to_web(response_msg, "response")
        
        response_data = f"{json.dumps(response.json(), indent=2)}"
        print(f"{RESPONSE_COLOR}{response_data}{RESET_COLOR}")
        log_to_web(response_data, "response")
        
        return response.json()
    except Exception as e:
        error_msg = f"❌ Error in proxy request: {str(e)}"
        print(f"\n{ERROR_COLOR}{error_msg}{RESET_COLOR}")
        log_to_web(error_msg, "error")
        raise

@app.route('/logs')
def logs_page():
    return render_template('logs.html')

@app.route('/logs/stream')
def stream_logs():
    def generate():
        q = Queue()
        with clients_lock:
            clients.add(q)
        try:
            while True:
                message = q.get()
                yield f"data: {json.dumps(message)}\n\n"
        finally:
            with clients_lock:
                clients.remove(q)
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/v1/chat/completions', methods=['OPTIONS', 'POST'])
@app.route('/chat/completions', methods=['OPTIONS', 'POST'])
def chat_completions():
    if request.method == 'OPTIONS':
        response = jsonify({
            "status": "ok",
            "message": "CORS preflight request successful"
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'POST,OPTIONS')
        response.headers.add('Access-Control-Max-Age', '3600')
        return response
    
    try:
        data = request.get_json()
        stream = data.get('stream', False)
        requested_model = data.get('model', 'gemma3:12b-it-qat')  # Get requested model
        
        # Get available models from Ollama
        try:
            models_response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
            models_data = models_response.json()
            available_models = [model['name'] for model in models_data.get('models', [])]
            
            # If requested model doesn't exist, use first available model
            if not available_models:
                raise Exception("No models available from Ollama")
                
            if requested_model not in available_models:
                original_model = requested_model
                requested_model = available_models[0]
                print(f"\n{REQUEST_COLOR}Model '{original_model}' not found. Using '{requested_model}' instead. View available models at http://localhost:7005/logs{RESET_COLOR}")
                log_to_web(f"Model '{original_model}' not found. Using '{requested_model}' instead. View available models at http://localhost:7005/logs", "warning")
        except Exception as e:
            print(f"\n{ERROR_COLOR}Error getting models list: {str(e)}. Using default model.{RESET_COLOR}")
            requested_model = 'gemma3:12b-it-qat'  # Fallback to default
        
        # Transform OpenAI format to Ollama format
        messages = data.get('messages', [])
        prompt = ""
        for message in messages:
            role = message.get('role', 'user')
            content = message.get('content', '')
            prompt += f"{role}: {content}\n"
        
        ollama_data = {
            "model": requested_model,  # Use the selected model
            "prompt": prompt,
            "stream": stream
        }
        
        if stream:
            ollama_response = proxy_request('POST', '/api/generate', ollama_data, stream=True)
            
            def generate():
                full_response = ""
                completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
                created = int(time.time())
                
                # Send initial role chunk
                response_data = {
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'created': created,
                    'model': data.get('model', 'gemma3:12b-it-qat'),
                    'choices': [{
                        'index': 0,
                        'delta': {'role': 'assistant'},
                        'finish_reason': None
                    }]
                }
                yield f"data: {json.dumps(response_data)}\n\n"
                
                # Process Ollama's streaming response
                for line in ollama_response.iter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            if 'response' in chunk:
                                response_text = chunk['response']
                                full_response += response_text
                                
                                # Log streaming chunk
                                print(f"{RESPONSE_COLOR}📝 Streaming chunk: {response_text}{RESET_COLOR}")
                                log_to_web(response_text, "stream_chunk")
                                
                                # Send content chunk
                                chunk_data = {
                                    'id': completion_id,
                                    'object': 'chat.completion.chunk',
                                    'created': created,
                                    'model': data.get('model', 'gemma3:12b-it-qat'),
                                    'choices': [{
                                        'index': 0,
                                        'delta': {'content': response_text},
                                        'finish_reason': None
                                    }]
                                }
                                yield f"data: {json.dumps(chunk_data)}\n\n"
                        except json.JSONDecodeError:
                            continue
                
                # Log stream end
                log_to_web("Stream completed", "stream_end")
                
                # Send final chunk
                final_chunk = {
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'created': created,
                    'model': data.get('model', 'gemma3:12b-it-qat'),
                    'choices': [{
                        'index': 0,
                        'delta': {},
                        'finish_reason': 'stop'
                    }]
                }
                yield f"data: {json.dumps(final_chunk)}\n\n"
                
                # Send usage chunk
                prompt_tokens = len(prompt) // 4
                completion_tokens = len(full_response) // 4
                usage_chunk = {
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'created': created,
                    'model': data.get('model', 'gemma3:12b-it-qat'),
                    'choices': [{
                        'index': 0,
                        'delta': {},
                        'finish_reason': None
                    }],
                    'usage': {
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                        'total_tokens': prompt_tokens + completion_tokens,
                        'prompt_tokens_details': {
                            'cached_tokens': 0,
                            'audio_tokens': 0
                        },
                        'completion_tokens_details': {
                            'reasoning_tokens': 0,
                            'audio_tokens': 0,
                            'accepted_prediction_tokens': 0,
                            'rejected_prediction_tokens': 0
                        }
                    }
                }
                yield f"data: {json.dumps(usage_chunk)}\n\n"
                
                yield "data: [DONE]\n\n"
            
            return Response(stream_with_context(generate()), mimetype='text/event-stream')
        else:
            ollama_response = proxy_request('POST', '/api/generate', ollama_data)
            response_content = ollama_response.get('response', '')
            
            # Format response to match OpenAI API format
            prompt_tokens = len(prompt) // 4
            completion_tokens = len(response_content) // 4
            
            openai_response = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": data.get('model', 'gemma3:12b-it-qat'),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": response_content,
                            "refusal": None,
                            "annotations": []
                        },
                        "logprobs": None,
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "prompt_tokens_details": {
                        "cached_tokens": 0,
                        "audio_tokens": 0
                    },
                    "completion_tokens_details": {
                        "reasoning_tokens": 0,
                        "audio_tokens": 0,
                        "accepted_prediction_tokens": 0,
                        "rejected_prediction_tokens": 0
                    }
                },
                "service_tier": "default",
                "system_fingerprint": f"fp_{uuid.uuid4().hex[:8]}"
            }
            
            return jsonify(openai_response)
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/v1/models', methods=['OPTIONS', 'GET'])
@app.route('/models', methods=['OPTIONS', 'GET'])
def list_models():
    if request.method == 'OPTIONS':
        response = jsonify({
            "status": "ok",
            "message": "CORS preflight request successful"
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET,OPTIONS')
        response.headers.add('Access-Control-Max-Age', '3600')
        return response
    
    try:
        ollama_response = proxy_request('GET', '/api/tags')
        models = ollama_response.get('models', [])
        
        return jsonify({
            "object": "list",
            "data": [
                {
                    "id": model.get('name', ''),
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "ollama"
                }
                for model in models
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/models')
def get_models():
    try:
        # Get list of models from Ollama with timeout - silently
        models_response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if not models_response.ok:
            error_msg = f"Failed to get models list: {models_response.status_code} - {models_response.text}"
            print(f"\n{ERROR_COLOR}{error_msg}{RESET_COLOR}")
            log_to_web(error_msg, "error")
            return jsonify({"error": error_msg}), 500
            
        models_data = models_response.json()
        
        # Return models without status
        models = []
        for model in models_data.get('models', []):
            models.append({
                "name": model.get('name', ''),
                "details": model.get('details', {})
            })
        
        return jsonify({"models": models})
    except Exception as e:
        error_msg = f"Error getting models: {str(e)}"
        print(f"\n{ERROR_COLOR}{error_msg}{RESET_COLOR}")
        log_to_web(error_msg, "error")
        return jsonify({"error": error_msg}), 500

@app.route('/api/models/refresh', methods=['POST'])
def refresh_models():
    try:
        # Get list of models
        models_response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if not models_response.ok:
            return jsonify({"error": "Failed to get models list"}), 500
            
        models_data = models_response.json()
        
        # Check which model is currently loaded using a single request
        print(f"\n{REQUEST_COLOR}Checking which model is currently loaded{RESET_COLOR}")
        log_to_web("Checking which model is currently loaded")
        
        try:
            # Get currently loaded model
            show_response = requests.get(f"{OLLAMA_BASE_URL}/api/show", timeout=2)
            current_model = None
            if show_response.status_code == 200:
                current_model = show_response.json().get('model', {}).get('name')
                print(f"{RESPONSE_COLOR}Currently loaded model: {current_model}{RESET_COLOR}")
                log_to_web(f"Currently loaded model: {current_model}")
        except Exception as e:
            print(f"{ERROR_COLOR}Error checking loaded model: {str(e)}{RESET_COLOR}")
            current_model = None
        
        # Update status for all models
        results = []
        for model in models_data.get('models', []):
            model_name = model.get('name', '')
            running = model_name == current_model
            
            # Update cache
            model_status_cache[model_name] = running
            
            results.append({
                "name": model_name,
                "running": running,
                "details": model.get('details', {})
            })
        
        return jsonify({"models": results})
    except Exception as e:
        error_msg = f"Error refreshing models: {str(e)}"
        print(f"\n{ERROR_COLOR}{error_msg}{RESET_COLOR}")
        log_to_web(error_msg, "error")
        return jsonify({"error": error_msg}), 500

@app.route('/api/model/control', methods=['POST'])
def control_model():
    try:
        data = request.get_json()
        model_name = data.get('model')
        action = data.get('action')
        
        if action == 'start':
            print(f"\n{REQUEST_COLOR}Starting model: {model_name}{RESET_COLOR}")
            log_to_web(f"Starting model: {model_name}")
            
            # Start model by sending a simple generation request
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": model_name,
                    "prompt": "You are a helpful AI assistant.",
                    "stream": False
                },
                timeout=30  # Longer timeout for model loading
            )
            
            if response.status_code == 200:
                success_msg = f"Model {model_name} start request sent"
                print(f"{RESPONSE_COLOR}{success_msg}{RESET_COLOR}")
                log_to_web(success_msg, "response")
                return jsonify({"status": "success", "message": success_msg})
            else:
                error_msg = f"Failed to start model: {response.status_code} - {response.text}"
                print(f"{ERROR_COLOR}{error_msg}{RESET_COLOR}")
                log_to_web(error_msg, "error")
                return jsonify({"error": error_msg}), 500
        else:
            return jsonify({"error": "Invalid action"}), 400
            
    except requests.exceptions.Timeout:
        error_msg = f"Timeout while starting model {model_name}. This is normal for the first load."
        print(f"\n{ERROR_COLOR}{error_msg}{RESET_COLOR}")
        log_to_web(error_msg, "error")
        return jsonify({"status": "pending", "message": error_msg}), 202
    except Exception as e:
        error_msg = f"Error controlling model: {str(e)}"
        print(f"\n{ERROR_COLOR}{error_msg}{RESET_COLOR}")
        log_to_web(error_msg, "error")
        return jsonify({"error": error_msg}), 500

# Add a catch-all route for other OpenAI API endpoints
@app.route('/v1/<path:path>', methods=['OPTIONS', 'GET', 'POST', 'PUT', 'DELETE'])
@app.route('/<path:path>', methods=['OPTIONS', 'GET', 'POST', 'PUT', 'DELETE'])
def catch_all(path):
    if request.method == 'OPTIONS':
        response = jsonify({
            "status": "ok",
            "message": "CORS preflight request successful"
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        response.headers.add('Access-Control-Max-Age', '3600')
        return response
    
    try:
        method = request.method
        data = request.get_json() if request.is_json else None
        stream = data.get('stream', False) if data else False
        
        ollama_response = proxy_request(method, f'/api/{path}', data, stream)
        
        if stream:
            def generate():
                for line in ollama_response.iter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            yield f"data: {json.dumps(chunk)}\n\n"
                        except json.JSONDecodeError:
                            continue
                yield "data: [DONE]\n\n"
            
            return Response(stream_with_context(generate()), mimetype='text/event-stream')
        else:
            return jsonify(ollama_response)
    
    except Exception as e:
        return jsonify({
            "error": {
                "message": str(e),
                "type": "invalid_request_error",
                "param": None,
                "code": None
            }
        }), 500

@app.route('/favicon.ico')
def favicon():
    return '', 204  # Return empty response with "No Content" status

@app.route('/')
def root():
    return redirect('/logs')

@app.route('/api/generate', methods=['POST'])
def generate():
    try:
        data = request.get_json()
        model_name = data.get('model')
        prompt = data.get('prompt')
        
        if not model_name or not prompt:
            return jsonify({"error": "Missing model name or prompt"}), 400
        
        print(f"\n{REQUEST_COLOR}Sending query to model {model_name}{RESET_COLOR}")
        log_to_web(f"Sending query to model {model_name}")
        
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "stream": False
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"{RESPONSE_COLOR}Got response from model{RESET_COLOR}")
            return jsonify(result)
        else:
            error_msg = f"Error from Ollama: {response.status_code} - {response.text}"
            print(f"{ERROR_COLOR}{error_msg}{RESET_COLOR}")
            log_to_web(error_msg, "error")
            return jsonify({"error": error_msg}), 500
            
    except Exception as e:
        error_msg = f"Error generating response: {str(e)}"
        print(f"\n{ERROR_COLOR}{error_msg}{RESET_COLOR}")
        log_to_web(error_msg, "error")
        return jsonify({"error": error_msg}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7005) 