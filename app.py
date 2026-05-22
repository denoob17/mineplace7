from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Pixel
from datetime import datetime, timedelta
import os

COOLDOWN_SECONDS = 1 

app = Flask(__name__)
app.config['SECRET_KEY'] = 'mineplace-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///canvas.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
socketio = SocketIO(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ───── Страницы ─────

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=current_user.username, is_admin=current_user.is_admin)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='Имя пользователя уже занято')
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('index'))
        return render_template('login.html', error='Неверный логин или пароль')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    pixels = Pixel.query.order_by(Pixel.id.desc()).limit(200).all()
    users = User.query.all()
    total = Pixel.query.count()
    return render_template('admin.html', pixels=pixels, users=users, total=total)

# ───── API (CRUD) ─────

@app.route('/api/canvas', methods=['GET'])          # READ
@login_required
def get_canvas():
    pixels = Pixel.query.all()
    return jsonify([p.to_dict() for p in pixels])

@app.route('/api/pixel', methods=['POST'])          # CREATE
@login_required
def create_pixel():
    # Проверка cooldown
    if current_user.last_pixel_at:
        elapsed = (datetime.utcnow() - current_user.last_pixel_at).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            wait = round(COOLDOWN_SECONDS - elapsed, 1)
            return jsonify({'error': f'Подожди {wait} сек'}), 429

    data = request.json
    x, y, color = data['x'], data['y'], data['color']
    # Если пиксель уже есть — обновляем (UPDATE)
    existing = Pixel.query.filter_by(x=x, y=y).first()
    if existing:
        existing.color = color
        existing.user_id = current_user.id
        db.session.commit()
        pixel = existing
    else:
        pixel = Pixel(x=x, y=y, color=color, user_id=current_user.id)
        db.session.add(pixel)
        db.session.commit()

    # Обновляем время последнего пикселя
    current_user.last_pixel_at = datetime.utcnow()
    db.session.commit()

    return jsonify(pixel.to_dict())

@app.route('/api/pixel/<int:pixel_id>', methods=['DELETE'])   # DELETE
@login_required
def delete_pixel(pixel_id):
    if not current_user.is_admin:        
        return jsonify({'error': 'Forbidden'}), 403  
    pixel = Pixel.query.get_or_404(pixel_id)
    db.session.delete(pixel)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/canvas/clear', methods=['DELETE'])  # DELETE ALL (admin)
@login_required
def clear_canvas():
    if not current_user.is_admin:
        return jsonify({'error': 'Forbidden'}), 403
    Pixel.query.delete()
    db.session.commit()
    socketio.emit('canvas_cleared', {})
    return jsonify({'ok': True})


# ───── WebSocket ─────

@socketio.on('draw')
def handle_draw(data):
    if not current_user.is_authenticated:
        return
    data['username'] = current_user.username
    emit('draw', data, broadcast=True, include_self=False)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True)