from flask import Flask, jsonify, render_template_string, request
import discord
from discord.ext import commands, tasks
import json
import os
import multiprocessing
import time
from datetime import datetime
import asyncio
from dotenv import load_dotenv

# --- Muat Environment Variables ---
load_dotenv() # Ini untuk menjalankan di lokal, Railway akan mengabaikannya

# --- Konfigurasi ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PROGRESS_CHANNEL_ID = int(os.environ.get("PROGRESS_CHANNEL_ID", 0))
API_SECRET_KEY = os.environ.get("API_SECRET_KEY")

# --- File Penyimpanan ---
# Cek apakah kita berjalan di Railway, jika ya, gunakan volume
if "RAILWAY_PROJECT_ID" in os.environ:
    DATA_DIR = "/data"
else:
    DATA_DIR = "." # Jika tidak, gunakan folder yang sama (untuk tes di lokal)

PROGRESS_FILE = os.path.join(DATA_DIR, "progress_data_multitask.json")
PUBLIC_MESSAGE_ID_FILE = os.path.join(DATA_DIR, "public_message_id.json")
UPDATE_QUEUE_FILE = os.path.join(DATA_DIR, "update_queue.json")
LOCAL_PROGRESS_FILE_FOR_SEEDING = "progress_data_multitask.json" # Nama file data lokal Tuan

# --- Fungsi Bantuan Global ---
def load_data(file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0: return {}
    with open(file_path, 'r', encoding='utf-8') as f:
        try: return json.load(f)
        except json.JSONDecodeError: return {}

def save_data(data, file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

def trigger_bot_update():
    # Log Diagnostik Ditambahkan
    print(f"TRIGGER @ {datetime.now()}: Menulis pembaruan ke {UPDATE_QUEUE_FILE}")
    save_data({"update_needed": True, "timestamp": time.time()}, UPDATE_QUEUE_FILE)

def seed_initial_data():
    """Menyalin data dari repositori ke volume HANYA PADA SAAT DIJALANKAN PERTAMA KALI."""
    if DATA_DIR != ".": # Hanya berjalan di lingkungan hosting (seperti Railway)
        # File penanda untuk mencegah seeding berulang kali
        SEED_FLAG_FILE = os.path.join(DATA_DIR, ".seed_complete")
        
        # Jika file penanda sudah ada, jangan lakukan apa-apa
        if os.path.exists(SEED_FLAG_FILE):
            print("Seeding data awal sudah pernah dilakukan, melewati...")
            return

        # Jika tidak ada penanda, lanjutkan proses seeding
        volume_file_path = PROGRESS_FILE
        repo_file_path = os.path.join(".", LOCAL_PROGRESS_FILE_FOR_SEEDING)
        
        if os.path.exists(repo_file_path):
            print("Data awal ditemukan di repositori, menyalin ke volume...")
            try:
                initial_data = load_data(repo_file_path)
                save_data(initial_data, volume_file_path)
                print("Data awal berhasil disalin.")
                
                # Buat file penanda setelah seeding berhasil
                with open(SEED_FLAG_FILE, 'w') as f:
                    f.write(str(datetime.now()))
                print(f"File penanda seeding dibuat di {SEED_FLAG_FILE}")

            except Exception as e:
                print(f"Gagal menyalin data awal: {e}")

# ==============================================================================
# BAGIAN KODE BOT DISCORD
# ==============================================================================
def run_bot():
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    def calculate_percentage(subtasks):
        if not subtasks: return 0
        completed = sum(1 for status in subtasks.values() if status)
        return round((completed / len(subtasks)) * 100)

    def generate_progress_bar(percentage):
        filled_blocks = int(round(percentage / 5))
        empty_blocks = 20 - filled_blocks
        # Menggunakan karakter khusus (zero-width space) untuk perataan dan mencegah pemotongan
        return '🟩' * filled_blocks + '⬜' * empty_blocks + '\u200b'


    def get_color_from_percentage(percentage):
        percentage = max(0, min(100, percentage))
        if percentage < 50:
            red = 255
            green = round(255 * (percentage / 50))
        else:
            red = round(255 * (1 - (percentage - 50) / 50))
            green = 255
        return discord.Color.from_rgb(red, green, 0)

    async def update_public_message(bot_instance):
        if not PROGRESS_CHANNEL_ID: return
        channel = bot_instance.get_channel(PROGRESS_CHANNEL_ID)
        if not channel: 
            print(f"Error: Channel dengan ID {PROGRESS_CHANNEL_ID} tidak ditemukan.")
            return
        
        all_tasks = load_data(PROGRESS_FILE)
        active_tasks = {name: data for name, data in all_tasks.items() if data.get("active")}
        
        if not active_tasks:
            embed = discord.Embed(title="🚀 Progress Pengembangan Game 🚀", description="Saat ini tidak ada proyek yang aktif.", color=discord.Color.greyple())
        else:
            all_overall_progress = [
                round(sum(calculate_percentage(cat.get("subtasks", {})) for cat in data.get("categories", {}).values()) / len(data.get("categories", {}))) if data.get("categories", {}) else 0 
                for data in active_tasks.values()
            ]
            avg_progress = round(sum(all_overall_progress) / len(all_overall_progress)) if all_overall_progress else 0
            
            dynamic_color = get_color_from_percentage(avg_progress)
            embed = discord.Embed(title="🚀 Progress Pengembangan Game 🚀", color=dynamic_color)
            
            description_parts = []
            for task_name, task_data in sorted(active_tasks.items()):
                categories = task_data.get("categories", {})
                overall_progress = round(sum(calculate_percentage(cat.get("subtasks", {})) for cat in categories.values()) / len(categories)) if categories else 0
                
                # Menambahkan header proyek ke deskripsi
                description_parts.append(f"__**PROYEK: {task_name.upper()}**__")
                description_parts.append(f"# {overall_progress}%")
                description_parts.append("\u200b") # Spasi kosong

                for cat_name, data in sorted(categories.items()):
                    percentage = calculate_percentage(data.get("subtasks", {}))
                    bar = generate_progress_bar(percentage)
                    description_parts.append(f"**{cat_name.capitalize()}**: {percentage}%")
                    description_parts.append(bar)
                
                description_parts.append("\n---\n") # Pemisah antar proyek

            if description_parts:
                description_parts.pop() # Hapus pemisah terakhir

            final_description = "\n".join(description_parts)
            
            # Memastikan tidak melebihi batas karakter deskripsi Discord
            if len(final_description) > 4096:
                final_description = final_description[:4093] + "..."
            
            embed.description = final_description


        msg_data = load_data(PUBLIC_MESSAGE_ID_FILE)
        msg_id = msg_data.get("message_id")
        message_to_edit = None

        if msg_id:
            try:
                message_to_edit = await channel.fetch_message(msg_id)
            except (discord.NotFound, discord.HTTPException):
                print(f"Pesan lama dengan ID {msg_id} tidak ditemukan. Akan membuat pesan baru.")
                message_to_edit = None
        
        try:
            if message_to_edit:
                await message_to_edit.edit(embed=embed)
            else:
                # Blok ini dijalankan jika msg_id tidak ada ATAU fetch_message gagal
                new_msg = await channel.send(embed=embed)
                save_data({"message_id": new_msg.id}, PUBLIC_MESSAGE_ID_FILE)
                print(f"Membuat atau mengganti pesan progres. ID Baru: {new_msg.id}")
        except discord.Forbidden:
            print(f"Error: Bot tidak memiliki izin untuk mengirim/mengedit pesan di channel {PROGRESS_CHANNEL_ID}.")


    @tasks.loop(seconds=5.0)
    async def check_for_updates():
        # Log Diagnostik Ditambahkan
        print(f"CHECK @ {datetime.now()}: Memeriksa pembaruan di {UPDATE_QUEUE_FILE}")
        update_queue = load_data(UPDATE_QUEUE_FILE)
        if update_queue.get("update_needed"):
            # Log Diagnostik Ditambahkan
            print(">>> PEMBARUAN DITEMUKAN! Memperbarui pesan Discord...")
            await update_public_message(bot)
            save_data({}, UPDATE_QUEUE_FILE)
            print(">>> ANTRIAN DIBERSIHKAN.")

    @bot.event
    async def on_ready():
        print(f'Bot Discord telah login sebagai {bot.user}')
        print('------')
        await update_public_message(bot)
        check_for_updates.start()

    if BOT_TOKEN:
        bot.run(BOT_TOKEN)
    else:
        print("Error: BOT_TOKEN tidak ditemukan. Pastikan sudah diatur di environment variables.")

# ==============================================================================
# BAGIAN KONTROL UI & API (Aplikasi Web Flask)
# ==============================================================================
app = Flask(__name__)
bot_process = None

# Template HTML tidak berubah
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dasbor Proyek</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: 'Inter', sans-serif; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; }
        .status-active { background-color: #22c55e; } .status-inactive { background-color: #ef4444; }
        .trello-container { display: flex; overflow-x: auto; padding-bottom: 1rem; gap: 1rem; }
        .trello-list { flex: 0 0 320px; max-width: 320px; background-color: #1f2937; border-radius: 0.5rem; display: flex; flex-direction: column; }
        .task-card { background-color: #374151; border-radius: 0.5rem; padding: 0.75rem; margin-bottom: 0.75rem; }
        .task-card.completed { text-decoration: line-through; color: #9ca3af; }
        .delete-btn { opacity: 0; transition: opacity 0.2s; }
        .task-card:hover .delete-btn { opacity: 1; }
        .tab { padding: 0.5rem 1rem; border-radius: 0.5rem 0.5rem 0 0; cursor: pointer; }
        .tab.active { background-color: #1f2937; }
        .config-btn { opacity: 0; transition: opacity 0.2s; }
        .trello-list:hover .config-btn { opacity: 1; }
    </style>
</head>
<body class="bg-gray-900 text-white p-4 h-screen flex flex-col">
    <!-- PANEL KONTROL BOT -->
    <div class="bg-gray-800 rounded-lg p-4 mb-6 flex-shrink-0 flex justify-between items-center">
        <div class="flex items-center space-x-3"><h2 class="text-xl font-bold">Panel Kontrol Bot</h2><div id="status-dot" class="status-dot status-inactive"></div><span id="status-text">Tidak Aktif</span></div>
        <div class="flex items-center space-x-2">
            <button onclick="loadTasks()" class="bg-gray-600 hover:bg-gray-700 text-white font-bold py-2 px-4 rounded-md text-sm">Refresh Data</button>
            <button id="start-btn" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded-md text-sm">Start</button>
            <button id="stop-btn" class="bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded-md text-sm">Stop</button>
            <button id="restart-btn" class="bg-orange-500 hover:bg-orange-600 text-white font-bold py-2 px-4 rounded-md text-sm">Restart</button>
        </div>
    </div>
    <!-- NAVIGASI TAB TUGAS -->
    <div class="flex border-b border-gray-700 mb-4 items-center">
        <div id="tabs-container" class="flex space-x-2"></div>
        <button onclick="showNewTaskModal()" class="ml-4 text-2xl font-bold text-gray-400 hover:text-white">+</button>
    </div>
    <!-- DASBOR PROYEK (TRELLO) -->
    <div id="trello-container" class="trello-container flex-grow"></div>
    <script>
        const API_SECRET = prompt("Masukkan Kunci API untuk mengakses dasbor:");
        let currentTask = null;
        // --- Fungsi Kontrol Bot ---
        const startBtn = document.getElementById('start-btn'), stopBtn = document.getElementById('stop-btn'), restartBtn = document.getElementById('restart-btn');
        const statusDot = document.getElementById('status-dot'), statusText = document.getElementById('status-text');
        function updateBotUI(status) {
            if (status === 'running') {
                statusDot.className = 'status-dot status-active'; statusText.textContent = 'Aktif';
                startBtn.disabled = true; stopBtn.disabled = false; restartBtn.disabled = false;
            } else {
                statusDot.className = 'status-dot status-inactive'; statusText.textContent = 'Tidak Aktif';
                startBtn.disabled = false; stopBtn.disabled = true; restartBtn.disabled = true;
            }
        }
        async function checkBotStatus() {
            try { const response = await fetch('/status'); updateBotUI((await response.json()).status); } catch (error) { console.error('Gagal memeriksa status bot:', error); updateBotUI('stopped'); }
        }
        startBtn.addEventListener('click', async () => { await fetch('/start', { method: 'POST' }); setTimeout(checkBotStatus, 1000); });
        stopBtn.addEventListener('click', async () => { await fetch('/stop', { method: 'POST' }); setTimeout(checkBotStatus, 1000); });
        restartBtn.addEventListener('click', async () => { await fetch('/stop', { method: 'POST' }); setTimeout(async () => { await fetch('/start', { method: 'POST' }); setTimeout(checkBotStatus, 1000); }, 2000); });
        // --- Fungsi Dasbor Proyek ---
        async function apiFetch(endpoint, options = {}) {
            const defaultOptions = { headers: { 'Content-Type': 'application/json', 'X-API-KEY': API_SECRET } };
            const response = await fetch(`/api${endpoint}`, { ...defaultOptions, ...options });
            if (!response.ok) { const error = await response.json(); alert(`Error: ${error.message}`); throw new Error(error.message); }
            return response.json();
        }
        async function loadTasks() {
            const tasks = await apiFetch('/tasks');
            const tabsContainer = document.getElementById('tabs-container');
            tabsContainer.innerHTML = '';
            
            const taskNames = Object.keys(tasks).sort();
            if (taskNames.length === 0) {
                currentTask = null;
                renderBoard(null);
                return;
            }
            if (!currentTask || !tasks[currentTask]) {
                currentTask = taskNames[0];
            }
            taskNames.forEach(taskName => {
                const taskData = tasks[taskName];
                const tab = document.createElement('div');
                tab.className = `tab ${taskName === currentTask ? 'active' : ''}`;
                tab.innerHTML = `
                    <span class="${taskData.active ? 'text-green-400 font-bold' : ''}">${taskName.charAt(0).toUpperCase() + taskName.slice(1)}</span>
                    <button onclick="event.stopPropagation(); showTaskSettings('${taskName}', ${taskData.active})" class="ml-2 text-gray-400 hover:text-white">⚙️</button>
                `;
                tab.onclick = () => {
                    currentTask = taskName;
                    loadTasks();
                };
                tabsContainer.appendChild(tab);
            });
            renderBoard(tasks[currentTask]);
        }
        
        function renderBoard(taskData) {
            const container = document.getElementById('trello-container');
            container.innerHTML = '';
            if (!taskData) {
                container.innerHTML = '<p class="text-center w-full">Pilih atau buat sebuah tugas untuk memulai.</p>';
                return;
            }
            const categories = taskData.categories || {};
            const sortedCategories = Object.keys(categories).sort();
            for (const categoryName of sortedCategories) {
                const categoryData = categories[categoryName];
                const subtasks = categoryData.subtasks || {};
                const totalTasks = Object.keys(subtasks).length;
                const completedTasks = Object.values(subtasks).filter(Boolean).length;
                const percentage = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
                
                const list = document.createElement('div');
                list.className = 'trello-list';
                list.innerHTML = `
                    <div class="p-4 flex-grow overflow-y-auto">
                        <div class="flex justify-between items-center mb-2">
                            <h2 class="text-xl font-bold">${categoryName.charAt(0).toUpperCase() + categoryName.slice(1)}</h2>
                            <div class="config-btn">
                                <button onclick="manageNote('${categoryName}')" class="text-gray-400 hover:text-white">🗒️</button>
                                <button onclick="editCategoryName('${categoryName}')" class="text-gray-400 hover:text-white ml-2">✏️</button>
                                <button onclick="deleteCategory('${categoryName}')" class="text-gray-400 hover:text-white ml-2">🗑️</button>
                            </div>
                        </div>
                        <div class="w-full bg-gray-700 rounded-full h-2.5 mb-2">
                            <div class="bg-blue-600 h-2.5 rounded-full" style="width: ${percentage}%"></div>
                        </div>
                        <p class="text-xs text-gray-400 text-right mb-4">${completedTasks} / ${totalTasks} (${percentage}%)</p>
                        <div class="space-y-3 mb-4">
                            ${Object.keys(subtasks).sort().map(taskName => `
                                <div class="task-card ${subtasks[taskName] ? 'completed' : ''}">
                                    <div class="flex justify-between items-center">
                                        <span class="cursor-pointer flex-grow" onclick="toggleTask('${categoryName}', '${taskName}')">${taskName.charAt(0).toUpperCase() + taskName.slice(1)}</span>
                                        <button onclick="deleteTask('${categoryName}', '${taskName}')" class="delete-btn text-red-500 hover:text-red-400 font-bold">&times;</button>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    <div class="p-4 pt-0 mt-auto">
                        <div class="flex">
                            <input type="text" id="new-task-${categoryName}" class="flex-grow bg-gray-600 rounded-l-md px-3 py-1 text-sm focus:outline-none min-w-0" placeholder="Sub-tugas baru...">
                            <button onclick="addTask('${categoryName}')" class="bg-gray-500 hover:bg-gray-600 text-white font-bold py-1 px-3 rounded-r-md text-sm">Tambah</button>
                        </div>
                    </div>
                `;
                container.appendChild(list);
            }
            container.innerHTML += `<div class="trello-list p-4"><input type="text" id="new-category-name" class="w-full bg-gray-700 text-white rounded-md px-4 py-2 mb-2 focus:outline-none" placeholder="Nama Kategori Baru..."><button onclick="addCategory()" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded-md">Tambah Kategori</button></div>`;
        }
        
        function showNewTaskModal() {
            const taskName = prompt("Masukkan nama untuk tugas/proyek baru:");
            if (taskName && taskName.trim()) { createNewTask(taskName.trim()); }
        }
        function showTaskSettings(taskName, isActive) {
            const action = prompt(`Pengaturan untuk "${taskName}":\n1: ${isActive ? 'Nonaktifkan' : 'Aktifkan'}\n2: Ganti Nama\n3: Hapus Tugas`);
            if (action === '1') { activateTask(taskName); } 
            else if (action === '2') { editTaskName(taskName); } 
            else if (action === '3') { if (confirm(`ANDA YAKIN ingin menghapus seluruh tugas "${taskName}"? Tindakan ini tidak bisa dibatalkan.`)) { deleteTaskFull(taskName); } }
        }
        async function createNewTask(taskName) {
            await apiFetch('/task', { method: 'POST', body: JSON.stringify({ name: taskName }) });
            currentTask = taskName;
            loadTasks();
        }
        async function editTaskName(oldTaskName) {
            const newTaskName = prompt(`Masukkan nama baru untuk tugas "${oldTaskName}":`, oldTaskName);
            if (newTaskName && newTaskName.trim() && newTaskName.trim() !== oldTaskName) {
                await apiFetch(`/task/${oldTaskName}`, { method: 'PUT', body: JSON.stringify({ name: newTaskName.trim() }) });
                currentTask = newTaskName.trim().toLowerCase();
                loadTasks();
            }
        }
        async function activateTask(taskName) {
            await apiFetch(`/task/${taskName}/activate`, { method: 'PUT' });
            loadTasks();
        }
        async function deleteTaskFull(taskName) {
            await apiFetch(`/task/${taskName}`, { method: 'DELETE' });
            currentTask = null;
            loadTasks();
        }
        async function addCategory() {
            const input = document.getElementById('new-category-name');
            const categoryName = input.value.trim();
            if (!categoryName || !currentTask) return;
            await apiFetch(`/task/${currentTask}/category`, { method: 'POST', body: JSON.stringify({ name: categoryName }) });
            input.value = '';
            loadTasks();
        }
        async function editCategoryName(oldCategoryName) {
            if (!currentTask) return;
            const newCategoryName = prompt(`Masukkan nama baru untuk kategori "${oldCategoryName}":`, oldCategoryName);
            if (newCategoryName && newCategoryName.trim() && newCategoryName.trim() !== oldCategoryName) {
                await apiFetch(`/task/${currentTask}/category/${oldCategoryName}`, { method: 'PUT', body: JSON.stringify({ name: newCategoryName.trim() }) });
                loadTasks();
            }
        }
        async function deleteCategory(categoryName) {
            if (!currentTask) return;
            if (confirm(`Anda yakin ingin menghapus kategori "${categoryName}" dan semua isinya?`)) {
                await apiFetch(`/task/${currentTask}/category/${categoryName}`, { method: 'DELETE' });
                loadTasks();
            }
        }
        async function manageNote(categoryName) {
            if (!currentTask) return;
            const tasks = await apiFetch('/tasks');
            const currentNote = tasks[currentTask]?.categories[categoryName]?.note || '';
            const newNote = prompt(`Catatan untuk "${categoryName}":`, currentNote);
            if (newNote === null) return;
            if (newNote.trim() === '' && currentNote !== '') {
                await apiFetch(`/task/${currentTask}/category/${categoryName}/note`, { method: 'DELETE' });
            } else if (newNote.trim() !== currentNote) {
                await apiFetch(`/task/${currentTask}/category/${categoryName}/note`, { method: 'POST', body: JSON.stringify({ note: newNote.trim() }) });
            }
        }
        async function addTask(categoryName) {
            const input = document.getElementById(`new-task-${categoryName}`);
            const taskName = input.value.trim();
            if (!taskName || !currentTask) return;
            await apiFetch(`/task/${currentTask}/category/${categoryName}/task`, { method: 'POST', body: JSON.stringify({ name: taskName }) });
            input.value = '';
            loadTasks();
        }
        async function toggleTask(categoryName, taskName) {
            if (!currentTask) return;
            await apiFetch(`/task/${currentTask}/category/${categoryName}/task/${taskName}`, { method: 'PUT' });
            loadTasks();
        }
        async function deleteTask(categoryName, taskName) {
            if (!currentTask) return;
            if (confirm(`Anda yakin ingin menghapus sub-tugas "${taskName}"?`)) {
                await apiFetch(`/task/${currentTask}/category/${categoryName}/task/${taskName}`, { method: 'DELETE' });
                loadTasks();
            }
        }
        document.addEventListener('DOMContentLoaded', () => {
            checkBotStatus();
            loadTasks();
            setInterval(checkBotStatus, 5000);
        });
    </script>
</body>
</html>
"""

# --- API Endpoints ---
@app.route('/')
def index(): return render_template_string(HTML_TEMPLATE)

@app.before_request
def check_api_key():
    if request.path.startswith('/api'):
        if request.headers.get('X-API-KEY') != API_SECRET_KEY:
            return jsonify({"message": "Error: Kunci API tidak valid atau tidak ada."}), 401
    if request.path in ['/start', '/stop', '/status']:
        return

@app.route('/api/tasks', methods=['GET'])
def get_tasks(): return jsonify(load_data(PROGRESS_FILE))

@app.route('/api/task', methods=['POST'])
def create_task():
    data = request.json
    name = data.get('name', '').lower()
    if not name: return jsonify({"message": "Nama tugas tidak boleh kosong"}), 400
    all_tasks = load_data(PROGRESS_FILE)
    if name in all_tasks: return jsonify({"message": "Tugas dengan nama ini sudah ada"}), 409
    all_tasks[name] = {"active": False, "categories": {}}
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": "Tugas berhasil dibuat"}), 201

@app.route('/api/task/<string:task_name>', methods=['PUT'])
def edit_task_name(task_name):
    task_name = task_name.lower()
    data = request.json
    new_name = data.get('name', '').lower()
    if not new_name: return jsonify({"message": "Nama baru tidak boleh kosong"}), 400
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks: return jsonify({"message": "Tugas tidak ditemukan"}), 404
    if new_name != task_name and new_name in all_tasks: return jsonify({"message": "Nama tugas baru sudah ada"}), 409
    
    all_tasks[new_name] = all_tasks.pop(task_name)
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": "Nama tugas berhasil diubah"}), 200

@app.route('/api/task/<string:task_name>/activate', methods=['PUT'])
def activate_task(task_name):
    task_name = task_name.lower()
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks: return jsonify({"message": "Tugas tidak ditemukan"}), 404
    all_tasks[task_name]["active"] = not all_tasks[task_name].get("active", False)
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": f"Status aktivasi tugas '{task_name}' berhasil diubah"}), 200

@app.route('/api/task/<string:task_name>', methods=['DELETE'])
def delete_task_full(task_name):
    task_name = task_name.lower()
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks: return jsonify({"message": "Tugas tidak ditemukan"}), 404
    del all_tasks[task_name]
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": f"Tugas '{task_name}' berhasil dihapus"}), 200

@app.route('/api/task/<string:task_name>/category', methods=['POST'])
def add_category(task_name):
    task_name = task_name.lower()
    data = request.json
    name = data.get('name', '').lower()
    if not name: return jsonify({"message": "Nama kategori tidak boleh kosong"}), 400
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks: return jsonify({"message": "Tugas tidak ditemukan"}), 404
    if name in all_tasks[task_name]['categories']: return jsonify({"message": "Kategori sudah ada"}), 409
    all_tasks[task_name]['categories'][name] = {"subtasks": {}, "note": ""}
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": "Kategori berhasil ditambahkan"}), 201

@app.route('/api/task/<string:task_name>/category/<string:category_name>', methods=['PUT'])
def edit_category(task_name, category_name):
    task_name, category_name = task_name.lower(), category_name.lower()
    data = request.json
    new_name = data.get('name', '').lower()
    if not new_name: return jsonify({"message": "Nama baru tidak boleh kosong"}), 400
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks or category_name not in all_tasks[task_name]['categories']:
        return jsonify({"message": "Tugas atau kategori tidak ditemukan"}), 404
    if new_name != category_name and new_name in all_tasks[task_name]['categories']:
        return jsonify({"message": "Nama kategori baru sudah ada"}), 409
    
    all_tasks[task_name]['categories'][new_name] = all_tasks[task_name]['categories'].pop(category_name)
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": "Nama kategori berhasil diubah"}), 200

@app.route('/api/task/<string:task_name>/category/<string:category_name>', methods=['DELETE'])
def delete_category(task_name, category_name):
    task_name, category_name = task_name.lower(), category_name.lower()
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks or category_name not in all_tasks[task_name]['categories']:
        return jsonify({"message": "Tugas atau kategori tidak ditemukan"}), 404
    del all_tasks[task_name]['categories'][category_name]
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": "Kategori berhasil dihapus"}), 200

@app.route('/api/task/<string:task_name>/category/<string:category_name>/note', methods=['POST'])
def save_note(task_name, category_name):
    task_name, category_name = task_name.lower(), category_name.lower()
    data = request.json
    note = data.get('note', '')
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks or category_name not in all_tasks[task_name]['categories']:
        return jsonify({"message": "Tugas atau kategori tidak ditemukan"}), 404
    all_tasks[task_name]['categories'][category_name]['note'] = note
    save_data(all_tasks, PROGRESS_FILE)
    return jsonify({"message": "Catatan berhasil disimpan"}), 200

@app.route('/api/task/<string:task_name>/category/<string:category_name>/note', methods=['DELETE'])
def delete_note(task_name, category_name):
    task_name, category_name = task_name.lower(), category_name.lower()
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks or category_name not in all_tasks[task_name]['categories']:
        return jsonify({"message": "Tugas atau kategori tidak ditemukan"}), 404
    all_tasks[task_name]['categories'][category_name]['note'] = ""
    save_data(all_tasks, PROGRESS_FILE)
    return jsonify({"message": "Catatan berhasil dihapus"}), 200

@app.route('/api/task/<string:task_name>/category/<string:category_name>/task', methods=['POST'])
def add_task(task_name, category_name):
    task_name, category_name = task_name.lower(), category_name.lower()
    data = request.json
    subtask_name = data.get('name', '').lower()
    if not subtask_name: return jsonify({"message": "Nama sub-tugas tidak boleh kosong"}), 400
    all_tasks = load_data(PROGRESS_FILE)
    if task_name not in all_tasks or category_name not in all_tasks[task_name]['categories']: return jsonify({"message": "Tugas atau kategori tidak ditemukan"}), 404
    if subtask_name in all_tasks[task_name]['categories'][category_name]['subtasks']: return jsonify({"message": "Sub-tugas sudah ada"}), 409
    all_tasks[task_name]['categories'][category_name]['subtasks'][subtask_name] = False
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": "Sub-tugas berhasil ditambahkan"}), 201

@app.route('/api/task/<string:task_name>/category/<string:category_name>/task/<string:subtask_name>', methods=['PUT'])
def toggle_task(task_name, category_name, subtask_name):
    task_name, category_name, subtask_name = task_name.lower(), category_name.lower(), subtask_name.lower()
    all_tasks = load_data(PROGRESS_FILE)
    try:
        current_status = all_tasks[task_name]['categories'][category_name]['subtasks'][subtask_name]
        all_tasks[task_name]['categories'][category_name]['subtasks'][subtask_name] = not current_status
    except KeyError:
        return jsonify({"message": "Tugas, kategori, atau sub-tugas tidak ditemukan"}), 404
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": "Status sub-tugas berhasil diubah"}), 200

@app.route('/api/task/<string:task_name>/category/<string:category_name>/task/<string:subtask_name>', methods=['DELETE'])
def delete_subtask(task_name, category_name, subtask_name):
    task_name, category_name, subtask_name = task_name.lower(), category_name.lower(), subtask_name.lower()
    all_tasks = load_data(PROGRESS_FILE)
    try:
        del all_tasks[task_name]['categories'][category_name]['subtasks'][subtask_name]
    except KeyError:
        return jsonify({"message": "Tugas, kategori, atau sub-tugas tidak ditemukan"}), 404
    save_data(all_tasks, PROGRESS_FILE)
    trigger_bot_update()
    return jsonify({"message": "Sub-tugas berhasil dihapus"}), 200

# --- Kontrol Proses Bot ---
@app.route('/start', methods=['POST'])
def start_bot():
    global bot_process
    if bot_process and bot_process.is_alive(): return jsonify({"status": "already running"}), 400
    bot_process = multiprocessing.Process(target=run_bot)
    bot_process.start()
    return jsonify({"status": "started"})

@app.route('/stop', methods=['POST'])
def stop_bot():
    global bot_process
    if not bot_process or not bot_process.is_alive(): return jsonify({"status": "already stopped"}), 400
    bot_process.terminate()
    bot_process.join()
    bot_process = None
    return jsonify({"status": "stopped"})

@app.route('/status')
def status():
    global bot_process
    return jsonify({"status": "running" if bot_process and bot_process.is_alive() else "stopped"})

if __name__ == '__main__':
    seed_initial_data()
    print("======================================================")
    print("Aplikasi Kontrol Bot & Dasbor Web Siap!")
    print("Buka browser Anda dan pergi ke http://127.0.0.1:5000")
    print("======================================================")
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

