import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify
from openpyxl import Workbook
import os
import shutil
import schedule
import threading
import time

# === Настройки ===
# === Настройки ===
app = Flask(__name__)
app.secret_key = "super_secret_key_12345"
PASSWORD = "12345"

# Убедимся, что база и папки создаются в корне проекта
DB_PATH = "rent.db"
BACKUP_FOLDER = "backups"
STATIC_FOLDER = "static"

os.makedirs(BACKUP_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)

# --- Инициализация БД ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS properties (
                id INTEGER PRIMARY KEY,
                address TEXT NOT NULL,
                prop_type TEXT CHECK(prop_type IN ('apartment', 'house')) DEFAULT 'apartment',
                apartment_num TEXT,
                rent_due_day INTEGER DEFAULT 10
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                contact TEXT,
                property_id INTEGER,
                FOREIGN KEY (property_id) REFERENCES properties(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER,
                amount REAL NOT NULL,
                pay_date DATE NOT NULL,
                period TEXT NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS utilities (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                pay_date DATE NOT NULL,
                period TEXT NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """)

        # Проверим, пустая ли таблица properties — если да, добавим примеры
        cursor.execute("SELECT COUNT(*) FROM properties")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO properties (address, prop_type, apartment_num, rent_due_day) VALUES (?, ?, ?, ?)",
                           ("ул. Ленина, 15", "apartment", "1", 10))
            cursor.execute("INSERT INTO properties (address, prop_type, rent_due_day) VALUES (?, ?, ?)",
                           ("Загородное ш., д. 42", "house", 12))

            cursor.execute("INSERT INTO tenants (name, contact, property_id) VALUES (?, ?, ?)",
                           ("Иван Петров", "+79991234567", 1))
            cursor.execute("INSERT INTO tenants (name, contact, property_id) VALUES (?, ?, ?)",
                           ("Мария Сидорова", "+79991234568", 2))
        conn.commit()

# --- Резервное копирование ---
@app.route("/backup/create")
def create_backup():
    if not session.get("logged_in"):
        return jsonify({"status": "error", "message": "Не авторизован"}), 401

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_FOLDER, f"backup_{timestamp}.db")
    try:
        shutil.copy2(DB_PATH, backup_path)
        return jsonify({"status": "success", "file": os.path.basename(backup_path)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/backup/list")
def list_backups():
    if not session.get("logged_in"):
        return jsonify([])

    backups = []
    for f in sorted(os.listdir(BACKUP_FOLDER), reverse=True):
        if f.endswith(".db"):
            path = os.path.join(BACKUP_FOLDER, f)
            size = os.path.getsize(path)
            date = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d.%m.%Y %H:%M")
            backups.append({"name": f, "date": date, "size": f"{size // 1024} КБ"})
    return jsonify(backups)

@app.route("/backup/restore/<filename>", methods=["POST"])
def restore_backup(filename):
    if not session.get("logged_in"):
        return jsonify({"status": "error"}), 401

    backup_path = os.path.join(BACKUP_FOLDER, filename)
    if os.path.exists(backup_path):
        try:
            shutil.copy2(backup_path, DB_PATH)
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Файл не найден"}), 404

# --- Главная страница / Вход ---
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form["password"]
        if password == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="❌ Неверный пароль!")
    return render_template("login.html")

# --- Панель управления ---
@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    current_month = datetime.now().strftime("%Y-%m")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # Все арендаторы с объектами
        cursor.execute("""
            SELECT t.id, t.name, t.contact, p.address, p.apartment_num, p.prop_type
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            ORDER BY p.address, p.apartment_num
        """)
        tenants_data = cursor.fetchall()

        # Активные платежи за месяц
        cursor.execute("""
            SELECT t.id, t.name, p.amount, p.pay_date
            FROM tenants t
            LEFT JOIN payments p ON t.id = p.tenant_id AND substr(pay_date, 1, 7) = ?
            ORDER BY t.name
        """, (current_month,))
        payments = {row[0]: {"name": row[1], "amount": row[2], "pay_date": row[3]} for row in cursor.fetchall()}

        # Долги
        due_day = datetime.now().day
        cursor.execute("""
            SELECT t.name, p.address, p.apartment_num, p.rent_due_day
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            WHERE p.rent_due_day < ?
              AND NOT EXISTS (
                SELECT 1 FROM payments pmt
                WHERE pmt.tenant_id = t.id
                  AND substr(pmt.pay_date, 1, 7) = ?
              )
        """, (due_day, current_month))
        debts = cursor.fetchall()

        # Суммы
        cursor.execute("SELECT SUM(amount) FROM payments WHERE substr(pay_date, 1, 7) = ?", (current_month,))
        total_rent = cursor.fetchone()[0] or 0

        cursor.execute("SELECT SUM(amount) FROM utilities WHERE substr(pay_date, 1, 7) = ?", (current_month,))
        total_utilities = cursor.fetchone()[0] or 0

    return render_template(
        "dashboard.html",
        tenants=tenants_data,
        payments=payments,
        debts=debts,
        total_rent=total_rent,
        total_utilities=total_utilities,
        now=datetime.now().strftime("%B %Y")
    )

# --- Добавление платежа ---
@app.route("/add_payment", methods=["GET", "POST"])
def add_payment():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if request.method == "POST":
        tenant_id = int(request.form["tenant_id"])
        amount = float(request.form["amount"])
        pay_date = request.form["pay_date"]
        period = request.form["period"]

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO payments (tenant_id, amount, pay_date, period)
                VALUES (?, ?, ?, ?)
            """, (tenant_id, amount, pay_date, period))
            conn.commit()
        return redirect(url_for("dashboard"))

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.id, t.name, t.contact, p.address, p.apartment_num, p.prop_type
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            ORDER BY p.address
        """)
        tenants = cursor.fetchall()

    return render_template("add_payment.html", tenants=tenants)

# --- Отчёт по коммуналке ---
@app.route("/utilities")
def utilities():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    current_month = datetime.now().strftime("%Y-%m")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.type, u.amount, u.pay_date, t.name, u.period, u.id
            FROM utilities u
            JOIN tenants t ON u.tenant_id = t.id
            WHERE substr(u.pay_date, 1, 7) = ?
            ORDER BY u.pay_date
        """, (current_month,))
        rows = cursor.fetchall()

        type_names = {
            "electricity": "⚡ Электричество",
            "water": "💧 Вода",
            "gas": "🔥 Газ",
            "heating": "♨️ Отопление"
        }

        total = sum(row[1] for row in rows)

    return render_template("utilities.html", rows=rows, total=total, type_names=type_names)

# --- Добавление коммуналки ---
@app.route("/add_utility", methods=["GET", "POST"])
def add_utility():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if request.method == "POST":
        tenant_id = int(request.form["tenant_id"])
        utype = request.form["type"]
        amount = float(request.form["amount"])
        pay_date = request.form["pay_date"]

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO utilities (tenant_id, type, amount, pay_date, period)
                VALUES (?, ?, ?, ?, ?)
            """, (tenant_id, utype, amount, pay_date, pay_date[:7]))
            conn.commit()
        return redirect(url_for("utilities"))

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.id, t.name, t.contact, p.address, p.apartment_num, p.prop_type
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            ORDER BY t.name
        """)
        tenants = cursor.fetchall()

    return render_template("add_utility.html", tenants=tenants)

# --- Графики оплат ---
@app.route("/stats")
def stats():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT period, SUM(amount) FROM payments
            GROUP BY period ORDER BY period
        """)
        rent_data = cursor.fetchall()

        cursor.execute("""
            SELECT period, SUM(amount) FROM utilities
            GROUP BY period ORDER BY period
        """)
        util_data = cursor.fetchall()

    months = sorted(list(set([r[0] for r in rent_data] + [u[0] for u in util_data])))
    rent_series = [next((r[1] for r in rent_data if r[0] == m), 0) for m in months]
    util_series = [next((u[1] for u in util_data if u[0] == m), 0) for m in months]

    return render_template("stats.html", months=months, rent_series=rent_series, util_series=util_series)

# --- Выход ---
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# --- Экспорт в Excel ---
@app.route("/export/<table>")
def export(table):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    wb = Workbook()
    ws = wb.active

    current_month = datetime.now().strftime("%Y-%m")
    filename = f"{table}_{current_month}.xlsx"

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        if table == "payments":
            ws.append(["Арендатор", "Сумма", "Дата", "Период"])
            cursor.execute("""
                SELECT t.name, p.amount, p.pay_date, p.period
                FROM payments p
                JOIN tenants t ON p.tenant_id = t.id
                WHERE substr(pay_date, 1, 7) = ?
            """, (current_month,))
            for row in cursor.fetchall():
                ws.append(row)

        elif table == "utilities":
            ws.append(["Тип", "Арендатор", "Сумма", "Дата", "Период"])
            cursor.execute("""
                SELECT u.type, t.name, u.amount, u.pay_date, u.period
                FROM utilities u
                JOIN tenants t ON u.tenant_id = t.id
                WHERE substr(u.pay_date, 1, 7) = ?
            """, (current_month,))
            for utype, name, amount, pay_date, period in cursor.fetchall():
                display_type = {
                    "electricity": "Электричество",
                    "water": "Вода",
                    "gas": "Газ",
                    "heating": "Отопление"
                }.get(utype, utype)
                ws.append([display_type, name, amount, pay_date, period])

    wb.save(filename)

    return send_file(filename, as_attachment=True, download_name=filename)

# --- Редактирование платежа ---
@app.route("/edit_payment/<int:tenant_id>", methods=["GET", "POST"])
def edit_payment(tenant_id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    current_month = datetime.now().strftime("%Y-%m")

    if request.method == "POST":
        amount = float(request.form["amount"])
        pay_date = request.form["pay_date"]
        period = request.form["period"]

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            # Удаляем старый платёж за этот месяц
            cursor.execute("DELETE FROM payments WHERE tenant_id = ? AND substr(pay_date, 1, 7) = ?",
                           (tenant_id, current_month))
            # Добавляем новый
            cursor.execute("""
                INSERT INTO payments (tenant_id, amount, pay_date, period)
                VALUES (?, ?, ?, ?)
            """, (tenant_id, amount, pay_date, period))
            conn.commit()

        return redirect(url_for("dashboard"))

    # Получаем данные арендатора и его платёж
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.name, p.amount, p.pay_date, p.period
            FROM tenants t
            LEFT JOIN payments p ON t.id = p.tenant_id AND substr(pay_date, 1, 7) = ?
            WHERE t.id = ?
        """, (current_month, tenant_id))
        result = cursor.fetchone()

    if not result:
        return redirect(url_for("dashboard"))

    name, amount, pay_date, period = result
    if not amount:
        amount = ""
    if not pay_date:
        pay_date = datetime.now().strftime("%Y-%m-%d")
    if not period:
        period = current_month

    return render_template("edit_payment.html", tenant={"id": tenant_id, "name": name}, amount=amount,
                           pay_date=pay_date, period=period)

# --- Редактирование арендатора ---
@app.route("/edit_tenant/<int:tenant_id>", methods=["GET", "POST"])
def edit_tenant(tenant_id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        if request.method == "POST":
            # Данные арендатора
            name = request.form["name"]
            contact = request.form["contact"]

            # Данные объекта
            address = request.form["address"]
            apartment_num = request.form["apartment_num"] or None
            prop_type = request.form["prop_type"]

            # Получаем ID объекта, привязанного к этому арендатору
            cursor.execute("SELECT property_id FROM tenants WHERE id = ?", (tenant_id,))
            result = cursor.fetchone()
            if not result:
                return redirect(url_for("dashboard"))
            property_id = result[0]

            # Обновляем ТОЛЬКО этот объект недвижимости
            cursor.execute("""
                UPDATE properties 
                SET address = ?, apartment_num = ?, prop_type = ?
                WHERE id = ?
            """, (address, apartment_num, prop_type, property_id))

            # Обновляем арендатора
            cursor.execute("""
                UPDATE tenants 
                SET name = ?, contact = ?
                WHERE id = ?
            """, (name, contact, tenant_id))

            conn.commit()
            return redirect(url_for("dashboard"))

        # Получаем данные арендатора и его объекта
        cursor.execute("""
            SELECT t.id, t.name, t.contact, t.property_id,
                   p.address, p.apartment_num, p.prop_type
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
            WHERE t.id = ?
        """, (tenant_id,))
        row = cursor.fetchone()

    if not row:
        return redirect(url_for("dashboard"))

    tenant = {
        "id": row[0],
        "name": row[1],
        "contact": row[2]
    }
    property = {
        "id": row[3],
        "address": row[4],
        "apartment_num": row[5],
        "prop_type": row[6]
    }

    return render_template("edit_tenant.html", tenant=tenant, property=property)

# --- Добавление арендатора ---
@app.route("/add_tenant", methods=["GET", "POST"])
def add_tenant():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form["name"]
        contact = request.form["contact"]
        address = request.form["address"]
        apartment_num = request.form["apartment_num"] or None
        prop_type = request.form["prop_type"]

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            # Сначала создаём объект недвижимости
            cursor.execute("""
                INSERT INTO properties (address, apartment_num, prop_type)
                VALUES (?, ?, ?)
            """, (address, apartment_num, prop_type))
            property_id = cursor.lastrowid

            # Затем создаём арендатора, привязанного к этому объекту
            cursor.execute("""
                INSERT INTO tenants (name, contact, property_id)
                VALUES (?, ?, ?)
            """, (name, contact, property_id))
            conn.commit()

        return redirect(url_for("dashboard"))

    return render_template("add_tenant.html")

# --- Отладка: просмотр всех арендаторов и объектов ---
@app.route("/debug")
def debug():
    if not session.get("logged_in"):
        return "No"

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.id, t.name, t.contact, t.property_id, p.id, p.address, p.apartment_num, p.prop_type
            FROM tenants t
            JOIN properties p ON t.property_id = p.id
        """)
        rows = cursor.fetchall()
        return "<pre>" + "\n".join([str(r) for r in rows]) + "</pre>"

@app.route("/export_db")
def export_db():
        if not session.get("logged_in"):
            return redirect(url_for("login"))

        # Проверим, существует ли файл
        if not os.path.exists(DB_PATH):
            return "❌ База данных не найдена", 404

        return send_file(
            DB_PATH,
            as_attachment=True,
            download_name=f"rent_backup_{datetime.now().strftime('%Y%m%d')}.db"
        )

# --- Автобэкап (каждый день в 3:00) ---
def backup_job():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_FOLDER, f"auto_backup_{timestamp}.db")
    try:
        shutil.copy2(DB_PATH, backup_path)
        print(f"✅ Автобэкап создан: {backup_path}")
    except Exception as e:
        print(f"❌ Ошибка бэкапа: {e}")

def start_scheduler():
    schedule.every().day.at("03:00").do(backup_job)
    while True:
        schedule.run_pending()
        time.sleep(60)

# Запуск планировщика в фоне
threading.Thread(target=start_scheduler, daemon=True).start()

# --- Инициализация при старте (для Render и локально) ---
init_db()
print("✅ База данных инициализирована")

# --- Запуск приложения ---
init_db()
print("✅ База данных инициализирована")

if __name__ == "__main__":
    print("🚀 Запуск сервера на http://127.0.0.1:5000")
    app.run(debug=False, host="0.0.0.0", port=5000)
