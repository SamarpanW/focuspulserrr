import os
import json
from flask import Flask, render_template_string, request, jsonify
from google.cloud import firestore
from groq import Groq

app = Flask(__name__)

# Safe lazy initialization helpers to prevent startup crash
def get_db():
    return firestore.Client()

def get_groq():
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set in Cloud Run.")
    return Groq(api_key=api_key)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FocusPulse | GCP Analytics</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-6 font-sans">
    <div class="max-w-4xl mx-auto space-y-8">
        <header class="border-b border-slate-800 pb-4 flex justify-between items-center">
            <div>
                <h1 class="text-3xl font-bold text-indigo-400">FocusPulse</h1>
                <p class="text-sm text-slate-400">Deployed on GCP Cloud Run & Firestore</p>
            </div>
            <span class="px-3 py-1 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-full text-xs font-semibold">GCP Native Active</span>
        </header>

        <div class="bg-slate-800 border border-slate-700 rounded-xl p-6 shadow-xl">
            <h2 class="text-lg font-semibold text-slate-200 mb-3">Log Productivity Activity</h2>
            <form id="logForm" class="space-y-4">
                <textarea id="rawText" rows="3" placeholder="e.g., Studied Operating Systems for 50 mins then spent 10 mins on Reels" required 
                    class="w-full bg-slate-900 border border-slate-700 rounded-lg p-3 text-slate-100 focus:outline-none focus:border-indigo-500"></textarea>
                <button type="submit" id="submitBtn" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-medium py-2.5 rounded-lg transition">
                    Analyze & Log via Groq AI
                </button>
            </form>
        </div>

        <div class="bg-slate-800 border border-slate-700 rounded-xl p-6 shadow-xl">
            <h2 class="text-lg font-semibold text-slate-200 mb-4">Firestore Activity Logs</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-left text-sm text-slate-300">
                    <thead class="bg-slate-900/50 text-slate-400 text-xs uppercase border-b border-slate-700">
                        <tr>
                            <th class="p-3">Category</th>
                            <th class="p-3">Raw Input</th>
                            <th class="p-3 text-center">Focus (Mins)</th>
                            <th class="p-3 text-center">Distraction (Mins)</th>
                        </tr>
                    </thead>
                    <tbody id="logsTable" class="divide-y divide-slate-700/50"></tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        async function fetchLogs() {
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                const tbody = document.getElementById('logsTable');
                tbody.innerHTML = '';
                
                if (Array.isArray(data)) {
                    data.forEach(log => {
                        const tr = document.createElement('tr');
                        tr.className = 'hover:bg-slate-700/30';
                        tr.innerHTML = `
                            <td class="p-3 font-semibold text-indigo-400">${log.category || 'DEEP_WORK'}</td>
                            <td class="p-3 text-slate-300">${log.raw_message}</td>
                            <td class="p-3 text-center text-emerald-400 font-mono">${log.focus_minutes}m</td>
                            <td class="p-3 text-center text-rose-400 font-mono">${log.distraction_minutes}m</td>
                        `;
                        tbody.appendChild(tr);
                    });
                }
            } catch (err) {
                console.error("Failed to fetch logs:", err);
            }
        }

        document.getElementById('logForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const text = document.getElementById('rawText').value;
            btn.disabled = true;
            btn.innerText = 'Processing with AI...';

            try {
                await fetch('/api/log', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text: text })
                });
                document.getElementById('rawText').value = '';
                fetchLogs();
            } catch (err) {
                alert("Error processing log: " + err);
            } finally {
                btn.disabled = false;
                btn.innerText = 'Analyze & Log via Groq AI';
            }
        });

        fetchLogs();
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/log', methods=['POST'])
def create_log():
    try:
        data = request.json or {}
        raw_text = data.get("text", "")
        if not raw_text:
            return jsonify({"error": "No input text provided"}), 400

        groq_client = get_groq()
        completion = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            response_format={"type": "json_object"},
            messages=[{
                "role": "system",
                "content": """Analyze raw log. Return JSON strictly with keys:
                category: one of ['DEEP_WORK', 'SHALLOW_WORK', 'LEISURE', 'PROCRASTINATION', 'PHYSICAL'],
                focus_minutes: int,
                distraction_minutes: int"""
            }, {"role": "user", "content": raw_text}]
        )

        parsed = json.loads(completion.choices[0].message.content)
        
        record = {
            "raw_message": raw_text,
            "category": parsed.get("category", "DEEP_WORK"),
            "focus_minutes": int(parsed.get("focus_minutes", 0)),
            "distraction_minutes": int(parsed.get("distraction_minutes", 0)),
            "timestamp": firestore.SERVER_TIMESTAMP
        }

        db = get_db()
        db.collection("focus_logs").add(record)
        return jsonify({"status": "success", "data": record}), 200
    except Exception as e:
        print(f"Error handling /api/log: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/logs', methods=['GET'])
def get_logs():
    try:
        db = get_db()
        docs = db.collection("focus_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(20).stream()
        logs = []
        for doc in docs:
            d = doc.to
