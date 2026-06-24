import os
import json
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, jsonify, session, send_from_directory
)
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
try:
    import cloudinary
    import cloudinary.uploader
    _CLOUDINARY_AVAILABLE = True
except ImportError:
    _CLOUDINARY_AVAILABLE = False

from models import (db, User, Metric, PointEntry, Event, EventAttendance,
                    AbsenceRequest, PointDispute, PushSubscription,
                    EventMessage, EventPoll, EventPollVote, EventMinutes,
                    DocumentFolder, ClubDocument)
from google_calendar import get_upcoming_events, sync_event_to_calendar, is_calendar_connected

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# ─── Cloudinary (file storage for Railway/production) ────────────────────────
if _CLOUDINARY_AVAILABLE and os.environ.get('CLOUDINARY_CLOUD_NAME'):
    cloudinary.config(
        cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
        api_key=os.environ.get('CLOUDINARY_API_KEY'),
        api_secret=os.environ.get('CLOUDINARY_API_SECRET'),
    )

def upload_to_cloudinary(file, folder, public_id=None, resource_type='auto'):
    """Upload to Cloudinary in production, or save locally in dev."""
    if _CLOUDINARY_AVAILABLE and os.environ.get('CLOUDINARY_CLOUD_NAME'):
        try:
            kwargs = {'folder': folder, 'resource_type': resource_type}
            if public_id:
                kwargs['public_id'] = public_id
                kwargs['overwrite'] = True
            result = cloudinary.uploader.upload(file, **kwargs)
            return result.get('secure_url')
        except Exception as e:
            print(f'Cloudinary upload error: {e}')
            return None
    # Local fallback: save to static/uploads
    local_folder = os.path.join(BASE_DIR, 'static', 'uploads', folder.split('/')[-1])
    os.makedirs(local_folder, exist_ok=True)
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'bin'
    fname = (f"{public_id}.{ext}" if public_id
             else f"{int(datetime.utcnow().timestamp())}_{secure_filename(file.filename)}")
    file.save(os.path.join(local_folder, fname))
    return f"/static/uploads/{folder.split('/')[-1]}/{fname}"

def delete_from_cloudinary(public_id, resource_type='image'):
    if _CLOUDINARY_AVAILABLE and os.environ.get('CLOUDINARY_CLOUD_NAME'):
        try:
            cloudinary.uploader.destroy(public_id, resource_type=resource_type)
        except Exception as e:
            print(f'Cloudinary delete error: {e}')

# ─── Push Notifications (pywebpush) ──────────────────────────────────────────
try:
    from pywebpush import webpush, WebPushException
    _PUSH_AVAILABLE = True
except ImportError:
    _PUSH_AVAILABLE = False

VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_PUBLIC_KEY  = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_CLAIMS      = {'sub': 'mailto:admin@rotaract-palghar.org'}


def _send_push(subscription_dict, title, body, url='/'):
    """Send a single push notification. Returns True on success."""
    if not _PUSH_AVAILABLE or not VAPID_PRIVATE_KEY:
        return False
    try:
        webpush(
            subscription_info=subscription_dict,
            data=json.dumps({'title': title, 'body': body, 'url': url,
                             'tag': 'rotaract-' + url.replace('/', '-')}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        return True
    except WebPushException as ex:
        if ex.response and ex.response.status_code in (404, 410):
            # Subscription expired — remove it
            sub = PushSubscription.query.filter_by(
                endpoint=subscription_dict.get('endpoint', '')).first()
            if sub:
                db.session.delete(sub)
                db.session.commit()
        return False
    except Exception:
        return False


def notify_user(user_id, title, body, url='/'):
    """Push to all devices of a single user."""
    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    for sub in subs:
        _send_push(sub.to_dict(), title, body, url)


def notify_all_members(title, body, url='/'):
    """Push to all active members (not admins)."""
    members = User.query.filter_by(is_active_member=True, role='member').all()
    for member in members:
        notify_user(member.id, title, body, url)


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'rotaract-palghar-secret-2024')

# Use PostgreSQL on Railway/Render (DATABASE_URL env var), else SQLite locally
_db_url = os.environ.get('DATABASE_URL',
    f"sqlite:///{os.path.join(os.path.dirname(__file__), 'rotaract.db')}")
# Railway gives postgres:// — SQLAlchemy needs postgresql://
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'avatars')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB max upload
BASE_DIR = os.path.dirname(__file__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MINUTES_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt'}
DOCUMENT_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
                        'txt', 'csv', 'png', 'jpg', 'jpeg', 'gif', 'zip', 'rar'}

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = ''


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ─── Context Processor — inject notification counts into every template ───────

@app.context_processor
def inject_notification_counts():
    if not current_user.is_authenticated:
        return dict(pending_absence_count=0, open_dispute_count=0)
    if current_user.role == 'admin':
        pending_absence_count = AbsenceRequest.query.filter_by(status='pending').count()
        open_dispute_count = PointDispute.query.filter_by(status='open').count()
    else:
        pending_absence_count = AbsenceRequest.query.filter_by(
            member_id=current_user.id).count()
        open_dispute_count = PointDispute.query.filter_by(
            member_id=current_user.id, status='open').count()
    return dict(
        pending_absence_count=pending_absence_count,
        open_dispute_count=open_dispute_count,
    )


# ─── Auth ────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password) and user.is_active_member:
            login_user(user, remember=True)
            return redirect(url_for('admin_dashboard') if user.role == 'admin' else url_for('dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ─── Member Views ─────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('dashboard'))


@app.route('/dashboard')
@login_required
def dashboard():
    members = User.query.filter_by(is_active_member=True).all()
    leaderboard = sorted(members, key=lambda m: m.total_points, reverse=True)
    my_rank = next((i + 1 for i, m in enumerate(leaderboard) if m.id == current_user.id), None)
    upcoming_events = Event.query.filter(
        Event.date >= datetime.utcnow()
    ).order_by(Event.date).limit(5).all()
    recent_activity = PointEntry.query.filter_by(
        member_id=current_user.id
    ).order_by(PointEntry.created_at.desc()).limit(8).all()
    calendar_events = get_upcoming_events(max_results=5)
    cal_connected = is_calendar_connected()

    # My absence requests for upcoming events
    my_absence_requests = {
        r.event_id: r for r in AbsenceRequest.query.filter_by(member_id=current_user.id).all()
    }

    return render_template(
        'dashboard.html',
        leaderboard=leaderboard[:10],
        my_rank=my_rank,
        upcoming_events=upcoming_events,
        recent_activity=recent_activity,
        calendar_events=calendar_events,
        cal_connected=cal_connected,
        my_absence_requests=my_absence_requests,
        today_date=date.today(),
    )


# ─── Profile ──────────────────────────────────────────────────────────────────

@app.route('/profile')
@login_required
def profile():
    my_disputes = PointDispute.query.filter_by(
        member_id=current_user.id
    ).order_by(PointDispute.created_at.desc()).all()
    my_absence = AbsenceRequest.query.filter_by(
        member_id=current_user.id
    ).order_by(AbsenceRequest.created_at.desc()).all()
    return render_template('member/profile.html',
                           my_disputes=my_disputes, my_absence=my_absence)


@app.route('/profile/picture', methods=['POST'])
@login_required
def upload_profile_picture():
    if 'picture' not in request.files:
        flash('No file selected.', 'error')
        return redirect(url_for('profile'))
    file = request.files['picture']
    if not file.filename:
        flash('No file selected.', 'error')
        return redirect(url_for('profile'))
    if not allowed_file(file.filename):
        flash('Invalid file type. Allowed: PNG, JPG, GIF, WEBP.', 'error')
        return redirect(url_for('profile'))

    url = upload_to_cloudinary(
        file, folder='rotaract/avatars',
        public_id=f'user_{current_user.id}', resource_type='image'
    )
    if not url:
        flash('Upload failed. Please try again.', 'error')
        return redirect(url_for('profile'))

    current_user.profile_picture = url
    db.session.commit()
    flash('Profile picture updated!', 'success')
    return redirect(url_for('profile'))


@app.route('/profile/picture/remove', methods=['POST'])
@login_required
def remove_profile_picture():
    if current_user.profile_picture:
        delete_from_cloudinary(f'rotaract/avatars/user_{current_user.id}', resource_type='image')
        current_user.profile_picture = None
        db.session.commit()
    flash('Profile picture removed.', 'success')
    return redirect(url_for('profile'))


# ─── Member — Absence Requests ────────────────────────────────────────────────

@app.route('/absence')
@login_required
def member_absence():
    upcoming_events = Event.query.filter(
        Event.date >= datetime.utcnow()
    ).order_by(Event.date).all()
    my_requests = AbsenceRequest.query.filter_by(
        member_id=current_user.id
    ).order_by(AbsenceRequest.created_at.desc()).all()
    requested_event_ids = {r.event_id for r in my_requests}
    return render_template('member/absence.html',
                           upcoming_events=upcoming_events,
                           my_requests=my_requests,
                           requested_event_ids=requested_event_ids)


@app.route('/absence/request', methods=['POST'])
@login_required
def submit_absence_request():
    event_id = request.form.get('event_id', type=int)
    reason = request.form.get('reason', '').strip()
    if not event_id or not reason:
        flash('Please select an event and provide a reason.', 'error')
        return redirect(url_for('member_absence'))
    existing = AbsenceRequest.query.filter_by(
        member_id=current_user.id, event_id=event_id).first()
    if existing:
        flash('You already submitted an absence request for this event.', 'error')
        return redirect(url_for('member_absence'))
    req = AbsenceRequest(member_id=current_user.id, event_id=event_id, reason=reason)
    db.session.add(req)
    db.session.commit()
    flash('Absence request submitted. Admin will review it shortly.', 'success')
    return redirect(url_for('member_absence'))


@app.route('/absence/<int:req_id>/cancel', methods=['POST'])
@login_required
def cancel_absence_request(req_id):
    req = AbsenceRequest.query.get_or_404(req_id)
    if req.member_id != current_user.id:
        flash('Not authorised.', 'error')
        return redirect(url_for('member_absence'))
    if req.status != 'pending':
        flash('Cannot cancel a request that has already been reviewed.', 'error')
        return redirect(url_for('member_absence'))
    db.session.delete(req)
    db.session.commit()
    flash('Absence request cancelled.', 'success')
    return redirect(url_for('member_absence'))


# ─── Member — Point Disputes ──────────────────────────────────────────────────

@app.route('/disputes')
@login_required
def member_disputes():
    my_disputes = PointDispute.query.filter_by(
        member_id=current_user.id
    ).order_by(PointDispute.created_at.desc()).all()
    metrics = Metric.query.filter_by(is_active=True).order_by(Metric.name).all()
    recent_events = (
        Event.query.filter(Event.date <= datetime.utcnow())
        .order_by(Event.date.desc()).limit(20).all()
    )
    return render_template('member/disputes.html',
                           my_disputes=my_disputes,
                           metrics=metrics,
                           recent_events=recent_events)


@app.route('/disputes/add', methods=['POST'])
@login_required
def submit_dispute():
    subject = request.form.get('subject', '').strip()
    message = request.form.get('message', '').strip()
    if not subject or not message:
        flash('Subject and message are required.', 'error')
        return redirect(url_for('member_disputes'))
    dispute = PointDispute(
        member_id=current_user.id,
        subject=subject,
        message=message,
        metric_name=request.form.get('metric_name', ''),
        event_name=request.form.get('event_name', ''),
    )
    db.session.add(dispute)
    db.session.commit()
    flash('Your complaint/question has been submitted. Admin will respond soon.', 'success')
    return redirect(url_for('member_disputes'))


# ─── Admin Views ──────────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
@admin_required
def admin_index():
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    members = User.query.filter_by(is_active_member=True).all()
    leaderboard = sorted(members, key=lambda m: m.total_points, reverse=True)
    total_points = sum(m.total_points for m in members)
    avg_points = round(total_points / len(members), 1) if members else 0
    today = date.today()
    month_start = date(today.year, today.month, 1)
    events_this_month = Event.query.filter(Event.date >= month_start).count()
    recent_entries = PointEntry.query.order_by(PointEntry.created_at.desc()).limit(15).all()
    upcoming_events = Event.query.filter(
        Event.date >= datetime.utcnow()
    ).order_by(Event.date).limit(5).all()
    cal_connected = is_calendar_connected()
    all_members = User.query.filter_by(is_active_member=True, role='member').all()
    fees_paid_count = sum(1 for m in all_members if m.fees_status == 'paid')
    fees_partial_count = sum(1 for m in all_members if m.fees_status == 'partial')
    fees_unpaid_count = sum(1 for m in all_members if m.fees_status == 'unpaid')
    fees_paid_total = sum(m.fees_paid_amount for m in all_members if m.fees_status in ('paid', 'partial'))
    return render_template(
        'admin/dashboard.html',
        leaderboard=leaderboard,
        total_members=len(members),
        total_points=total_points,
        avg_points=avg_points,
        events_this_month=events_this_month,
        recent_entries=recent_entries,
        upcoming_events=upcoming_events,
        cal_connected=cal_connected,
        fees_paid_count=fees_paid_count,
        fees_partial_count=fees_partial_count,
        fees_unpaid_count=fees_unpaid_count,
        fees_paid_total=fees_paid_total,
    )


@app.route('/admin/members')
@login_required
@admin_required
def admin_members():
    members = User.query.order_by(User.name).all()
    return render_template('admin/members.html', members=members)


@app.route('/admin/members/add', methods=['POST'])
@login_required
@admin_required
def admin_members_add():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    position = request.form.get('position', 'Member')
    phone = request.form.get('phone', '')
    role = request.form.get('role', 'member')
    if not name or not email or not password:
        flash('Name, email, and password are required.', 'error')
        return redirect(url_for('admin_members'))
    if User.query.filter_by(email=email).first():
        flash('Email already registered.', 'error')
        return redirect(url_for('admin_members'))
    user = User(name=name, email=email, position=position, phone=phone, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'Member {name} added successfully.', 'success')
    return redirect(url_for('admin_members'))


@app.route('/admin/members/<int:member_id>/edit', methods=['POST'])
@login_required
@admin_required
def admin_members_edit(member_id):
    user = User.query.get_or_404(member_id)
    user.name = request.form.get('name', user.name).strip()
    user.position = request.form.get('position', user.position)
    user.phone = request.form.get('phone', user.phone)
    user.role = request.form.get('role', user.role)
    user.is_active_member = request.form.get('is_active') == 'on'
    new_password = request.form.get('password', '')
    if new_password:
        user.set_password(new_password)
    db.session.commit()
    flash(f'Member {user.name} updated.', 'success')
    return redirect(url_for('admin_members'))


@app.route('/admin/members/<int:member_id>/fees', methods=['POST'])
@login_required
@admin_required
def admin_members_fees(member_id):
    user = User.query.get_or_404(member_id)
    user.fees_status = request.form.get('fees_status', user.fees_status)
    user.fees_amount = int(request.form.get('fees_amount') or 0)
    user.fees_paid_amount = int(request.form.get('fees_paid_amount') or 0)
    paid_date_str = request.form.get('fees_paid_date', '')
    if paid_date_str:
        try:
            user.fees_paid_date = datetime.strptime(paid_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    else:
        user.fees_paid_date = None
    db.session.commit()
    flash(f'Fees updated for {user.name}.', 'success')
    return redirect(url_for('admin_members'))


@app.route('/admin/members/<int:member_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_members_delete(member_id):
    user = User.query.get_or_404(member_id)
    if user.id == current_user.id:
        flash('Cannot delete your own account.', 'error')
        return redirect(url_for('admin_members'))
    db.session.delete(user)
    db.session.commit()
    flash(f'Member {user.name} deleted.', 'success')
    return redirect(url_for('admin_members'))


@app.route('/admin/metrics')
@login_required
@admin_required
def admin_metrics():
    metrics = Metric.query.order_by(Metric.name).all()
    return render_template('admin/metrics.html', metrics=metrics)


@app.route('/admin/metrics/add', methods=['POST'])
@login_required
@admin_required
def admin_metrics_add():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Metric name is required.', 'error')
        return redirect(url_for('admin_metrics'))
    metric = Metric(
        name=name,
        description=request.form.get('description', ''),
        icon=request.form.get('icon', '⭐'),
        color=request.form.get('color', '#0072CE'),
        max_points=int(request.form.get('max_points', 100)),
    )
    db.session.add(metric)
    db.session.commit()
    flash(f'Metric "{name}" created.', 'success')
    return redirect(url_for('admin_metrics'))


@app.route('/admin/metrics/<int:metric_id>/edit', methods=['POST'])
@login_required
@admin_required
def admin_metrics_edit(metric_id):
    metric = Metric.query.get_or_404(metric_id)
    metric.name = request.form.get('name', metric.name).strip()
    metric.description = request.form.get('description', metric.description)
    metric.icon = request.form.get('icon', metric.icon)
    metric.color = request.form.get('color', metric.color)
    metric.max_points = int(request.form.get('max_points', metric.max_points))
    metric.is_active = request.form.get('is_active') == 'on'
    db.session.commit()
    flash(f'Metric "{metric.name}" updated.', 'success')
    return redirect(url_for('admin_metrics'))


@app.route('/admin/metrics/<int:metric_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_metrics_delete(metric_id):
    metric = Metric.query.get_or_404(metric_id)
    db.session.delete(metric)
    db.session.commit()
    flash(f'Metric "{metric.name}" deleted.', 'success')
    return redirect(url_for('admin_metrics'))


@app.route('/admin/points')
@login_required
@admin_required
def admin_points():
    members = User.query.filter_by(is_active_member=True).order_by(User.name).all()
    metrics = Metric.query.filter_by(is_active=True).order_by(Metric.name).all()
    entries = PointEntry.query.order_by(PointEntry.created_at.desc()).limit(50).all()
    return render_template('admin/points.html', members=members, metrics=metrics,
                           entries=entries, today=date.today().isoformat())


@app.route('/admin/points/add', methods=['POST'])
@login_required
@admin_required
def admin_points_add():
    member_id = int(request.form.get('member_id'))
    metric_id = int(request.form.get('metric_id'))
    points = int(request.form.get('points', 0))
    note = request.form.get('note', '')
    entry_date = request.form.get('date', date.today().isoformat())
    entry = PointEntry(
        member_id=member_id, metric_id=metric_id, points=points, note=note,
        date=date.fromisoformat(entry_date), added_by_id=current_user.id,
    )
    db.session.add(entry)
    db.session.commit()
    member = User.query.get(member_id)
    metric = Metric.query.get(metric_id)
    notify_user(
        member_id,
        f'{"+" if points >= 0 else ""}{points} pts — {metric.name if metric else ""}',
        note or 'Points updated by admin.',
        '/dashboard',
    )
    flash(f'{"+" if points >= 0 else ""}{points} points assigned to {member.name}.', 'success')
    return redirect(url_for('admin_points'))


@app.route('/admin/points/<int:entry_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_points_delete(entry_id):
    entry = PointEntry.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    flash('Point entry removed.', 'success')
    return redirect(url_for('admin_points'))


# ─── Admin — Events ───────────────────────────────────────────────────────────

@app.route('/admin/events')
@login_required
@admin_required
def admin_events():
    upcoming = Event.query.filter(Event.date >= datetime.utcnow()).order_by(Event.date).all()
    past = Event.query.filter(Event.date < datetime.utcnow()).order_by(Event.date.desc()).limit(20).all()
    cal_connected = is_calendar_connected()
    return render_template('admin/events.html',
                           upcoming=upcoming, past=past, cal_connected=cal_connected)


@app.route('/admin/events/add', methods=['POST'])
@login_required
@admin_required
def admin_events_add():
    title = request.form.get('title', '').strip()
    if not title:
        flash('Event title is required.', 'error')
        return redirect(url_for('admin_events'))
    event_date_str = request.form.get('date', '')
    try:
        event_date = datetime.fromisoformat(event_date_str)
    except ValueError:
        flash('Invalid date format.', 'error')
        return redirect(url_for('admin_events'))
    end_str = request.form.get('end_date', '')
    end_date = datetime.fromisoformat(end_str) if end_str else None
    event = Event(
        title=title,
        event_type=request.form.get('event_type', 'meeting'),
        date=event_date, end_date=end_date,
        description=request.form.get('description', ''),
        location=request.form.get('location', ''),
        points_for_attendance=int(request.form.get('points_for_attendance', 10)),
    )
    db.session.add(event)
    db.session.flush()
    gcal_id = sync_event_to_calendar(event)
    if gcal_id:
        event.google_calendar_id = gcal_id
    db.session.commit()
    notify_all_members(
        f'New Event: {title}',
        f'{event.date.strftime("%d %b")} · {event.location or "TBD"}',
        '/dashboard',
    )
    flash(f'Event "{title}" created.', 'success')
    return redirect(url_for('admin_events'))


@app.route('/admin/events/<int:event_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_events_delete(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash('Event deleted.', 'success')
    return redirect(url_for('admin_events'))


# ─── Admin — Event Detail + Bulk Attendance ───────────────────────────────────

@app.route('/admin/events/<int:event_id>')
@login_required
@admin_required
def admin_event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    members = User.query.filter_by(is_active_member=True).order_by(User.name).all()
    attended_ids = {a.member_id for a in event.attendances.filter_by(attended=True)}
    absence_requests = event.event_absence_requests.order_by(AbsenceRequest.created_at).all()
    absence_member_ids = {r.member_id for r in absence_requests}
    attendance_metric = Metric.query.filter_by(name='Event Attendance').first()
    return render_template(
        'admin/event_detail.html',
        event=event,
        members=members,
        attended_ids=attended_ids,
        absence_requests=absence_requests,
        absence_member_ids=absence_member_ids,
        attendance_metric=attendance_metric,
        today=date.today(),
    )


@app.route('/admin/events/<int:event_id>/bulk-attendance', methods=['POST'])
@login_required
@admin_required
def admin_bulk_attendance(event_id):
    event = Event.query.get_or_404(event_id)
    member_ids = [int(x) for x in request.form.getlist('member_ids')]

    # Clear and rebuild attendance
    EventAttendance.query.filter_by(event_id=event_id).delete()

    attendance_metric = Metric.query.filter_by(name='Event Attendance').first()
    points_note = f'Attended: {event.title}'

    for mid in member_ids:
        db.session.add(EventAttendance(event_id=event_id, member_id=mid, attended=True))
        if attendance_metric and event.points_for_attendance > 0:
            existing = PointEntry.query.filter_by(
                member_id=mid, metric_id=attendance_metric.id, note=points_note
            ).first()
            if not existing:
                db.session.add(PointEntry(
                    member_id=mid,
                    metric_id=attendance_metric.id,
                    points=event.points_for_attendance,
                    note=points_note,
                    date=event.date.date(),
                    added_by_id=current_user.id,
                ))

    db.session.commit()
    if attendance_metric and event.points_for_attendance > 0:
        for mid in member_ids:
            notify_user(
                mid,
                f'+{event.points_for_attendance} pts — {event.title}',
                'Your attendance has been marked and points awarded.',
                '/dashboard',
            )
    flash(
        f'Attendance saved: {len(member_ids)} present. '
        f'{"+" + str(event.points_for_attendance) + " pts each awarded." if attendance_metric else "No attendance metric found — create one to auto-award points."}',
        'success'
    )
    return redirect(url_for('admin_event_detail', event_id=event_id))


# ─── Admin — Absence Requests ─────────────────────────────────────────────────

@app.route('/admin/absence-requests')
@login_required
@admin_required
def admin_absence_requests():
    pending = AbsenceRequest.query.filter_by(
        status='pending'
    ).order_by(AbsenceRequest.created_at.desc()).all()
    history = AbsenceRequest.query.filter(
        AbsenceRequest.status != 'pending'
    ).order_by(AbsenceRequest.reviewed_at.desc()).limit(40).all()
    return render_template('admin/absence_requests.html', pending=pending, history=history)


@app.route('/admin/absence-requests/<int:req_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_absence(req_id):
    req = AbsenceRequest.query.get_or_404(req_id)
    req.status = 'approved'
    req.admin_note = request.form.get('note', '')
    req.reviewed_by_id = current_user.id
    req.reviewed_at = datetime.utcnow()
    db.session.commit()
    notify_user(
        req.member_id,
        'Absence Approved ✓',
        f'Your absence for "{req.event.title}" has been approved.',
        '/absence',
    )
    flash(f'Absence approved for {req.member.name}.', 'success')
    return redirect(url_for('admin_absence_requests'))


@app.route('/admin/absence-requests/<int:req_id>/reject', methods=['POST'])
@login_required
@admin_required
def reject_absence(req_id):
    req = AbsenceRequest.query.get_or_404(req_id)
    req.status = 'rejected'
    req.admin_note = request.form.get('note', '')
    req.reviewed_by_id = current_user.id
    req.reviewed_at = datetime.utcnow()
    db.session.commit()
    notify_user(
        req.member_id,
        'Absence Request Update',
        f'Your absence request for "{req.event.title}" was not approved.',
        '/absence',
    )
    flash(f'Absence request by {req.member.name} rejected.', 'success')
    return redirect(url_for('admin_absence_requests'))


# ─── Admin — Point Disputes ───────────────────────────────────────────────────

@app.route('/admin/disputes')
@login_required
@admin_required
def admin_disputes():
    open_disputes = PointDispute.query.filter_by(
        status='open'
    ).order_by(PointDispute.created_at.desc()).all()
    resolved = PointDispute.query.filter(
        PointDispute.status != 'open'
    ).order_by(PointDispute.resolved_at.desc()).limit(30).all()
    return render_template('admin/disputes.html',
                           open_disputes=open_disputes, resolved=resolved)


@app.route('/admin/disputes/<int:dispute_id>/respond', methods=['POST'])
@login_required
@admin_required
def respond_dispute(dispute_id):
    dispute = PointDispute.query.get_or_404(dispute_id)
    dispute.admin_response = request.form.get('response', '').strip()
    dispute.status = request.form.get('action', 'resolved')
    dispute.resolved_by_id = current_user.id
    dispute.resolved_at = datetime.utcnow()
    db.session.commit()
    notify_user(
        dispute.member_id,
        'Admin Replied to Your Complaint',
        f'"{dispute.subject}" — admin has responded.',
        '/disputes',
    )
    flash(f'Response sent to {dispute.member.name}.', 'success')
    return redirect(url_for('admin_disputes'))


@app.route('/admin/comparison')
@login_required
@admin_required
def admin_comparison():
    members = User.query.filter_by(is_active_member=True).order_by(User.name).all()
    return render_template('admin/comparison.html', members=members)


# ─── API Endpoints ────────────────────────────────────────────────────────────

@app.route('/api/leaderboard')
@login_required
def api_leaderboard():
    members = User.query.filter_by(is_active_member=True).all()
    data = sorted(
        [{'id': m.id, 'name': m.name, 'position': m.position,
          'points': m.total_points, 'initials': m.initials} for m in members],
        key=lambda x: x['points'], reverse=True
    )
    return jsonify(data)


@app.route('/api/chart-data')
@login_required
def api_chart_data():
    members = User.query.filter_by(is_active_member=True).all()
    leaderboard = sorted(members, key=lambda m: m.total_points, reverse=True)
    top_members = {
        'labels': [m.name.split()[0] for m in leaderboard[:10]],
        'data': [m.total_points for m in leaderboard[:10]],
        'colors': ['#F7A81B' if i == 0 else '#0072CE' if i < 3 else '#3b82f6'
                   for i in range(min(10, len(leaderboard)))]
    }
    metrics = Metric.query.filter_by(is_active=True).all()
    points_by_metric = {
        'labels': [m.name for m in metrics],
        'data': [m.total_distributed for m in metrics],
        'colors': [m.color for m in metrics]
    }
    monthly_trend = []
    today = date.today()
    for i in range(5, -1, -1):
        month_date = date(today.year, today.month, 1) - timedelta(days=i * 30)
        month_start = date(month_date.year, month_date.month, 1)
        month_end = date(month_date.year + 1, 1, 1) if month_date.month == 12 \
            else date(month_date.year, month_date.month + 1, 1)
        total = db.session.query(db.func.sum(PointEntry.points))\
            .filter(PointEntry.date >= month_start, PointEntry.date < month_end).scalar() or 0
        monthly_trend.append({'month': month_start.strftime('%b %Y'), 'points': total})
    event_types = db.session.query(
        Event.event_type, db.func.count(Event.id)
    ).group_by(Event.event_type).all()
    event_type_data = {
        'labels': [e[0].capitalize() for e in event_types],
        'data': [e[1] for e in event_types],
        'colors': ['#0072CE', '#F7A81B', '#10b981', '#8b5cf6']
    }
    return jsonify({
        'top_members': top_members,
        'points_by_metric': points_by_metric,
        'monthly_trend': monthly_trend,
        'event_types': event_type_data,
    })


@app.route('/api/member/<int:member_id>/stats')
@login_required
def api_member_stats(member_id):
    user = User.query.get_or_404(member_id)
    all_members = sorted(User.query.filter_by(is_active_member=True).all(),
                         key=lambda m: m.total_points, reverse=True)
    rank = next((i + 1 for i, m in enumerate(all_members) if m.id == user.id), None)
    recent = PointEntry.query.filter_by(member_id=member_id)\
        .order_by(PointEntry.created_at.desc()).limit(10).all()
    return jsonify({
        'id': user.id, 'name': user.name, 'position': user.position,
        'total_points': user.total_points,
        'points_this_month': user.points_this_month,
        'events_attended': user.events_attended,
        'rank': rank, 'total_members': len(all_members),
        'points_by_metric': user.points_by_metric(),
        'recent_activity': [
            {'points': e.points, 'metric': e.metric.name, 'note': e.note, 'date': e.date.isoformat()}
            for e in recent
        ]
    })


@app.route('/api/comparison')
@login_required
@admin_required
def api_comparison():
    member_ids = request.args.get('members', '')
    if not member_ids:
        return jsonify({'error': 'No members specified'}), 400
    ids = [int(x) for x in member_ids.split(',') if x.strip().isdigit()]
    members = User.query.filter(User.id.in_(ids)).all()
    metrics = Metric.query.filter_by(is_active=True).all()
    comparison = []
    today = date.today()
    month_labels = []
    for i in range(5, -1, -1):
        md = date(today.year, today.month, 1) - timedelta(days=i * 30)
        month_labels.append(date(md.year, md.month, 1).strftime('%b %Y'))
    for m in members:
        metric_points = {}
        for metric in metrics:
            pts = db.session.query(db.func.sum(PointEntry.points))\
                .filter(PointEntry.member_id == m.id, PointEntry.metric_id == metric.id).scalar() or 0
            metric_points[metric.name] = pts
        monthly = []
        for i in range(5, -1, -1):
            md = date(today.year, today.month, 1) - timedelta(days=i * 30)
            ms = date(md.year, md.month, 1)
            me = date(md.year + 1, 1, 1) if md.month == 12 else date(md.year, md.month + 1, 1)
            pts = db.session.query(db.func.sum(PointEntry.points))\
                .filter(PointEntry.member_id == m.id,
                        PointEntry.date >= ms, PointEntry.date < me).scalar() or 0
            monthly.append(pts)
        comparison.append({
            'id': m.id, 'name': m.name, 'position': m.position,
            'total_points': m.total_points, 'points_this_month': m.points_this_month,
            'events_attended': m.events_attended,
            'metric_points': metric_points, 'monthly_trend': monthly,
        })
    return jsonify({
        'members': comparison,
        'metric_labels': [m.name for m in metrics],
        'month_labels': month_labels,
    })


# ─── Member Events List ───────────────────────────────────────────────────────

@app.route('/events')
@login_required
def events_list():
    upcoming = Event.query.filter(Event.date >= datetime.utcnow()).order_by(Event.date).all()
    past = Event.query.filter(Event.date < datetime.utcnow()).order_by(Event.date.desc()).limit(20).all()
    today = date.today()
    return render_template('member/events_list.html', upcoming=upcoming, past=past, today=today)


# ─── Event Hub (chat, polls, minutes, days left) ─────────────────────────────

@app.route('/events/<int:event_id>')
@login_required
def event_hub(event_id):
    event = Event.query.get_or_404(event_id)
    messages = EventMessage.query.filter_by(event_id=event_id).order_by(EventMessage.created_at).all()
    polls = EventPoll.query.filter_by(event_id=event_id, is_active=True).order_by(EventPoll.created_at.desc()).all()
    mins = EventMinutes.query.filter_by(event_id=event_id).order_by(EventMinutes.created_at.desc()).all()
    my_absence = AbsenceRequest.query.filter_by(member_id=current_user.id, event_id=event_id).first()
    today = date.today()
    days_left = (event.date.date() - today).days
    # Build per-poll vote context
    poll_data = []
    for poll in polls:
        counts = poll.vote_counts()
        total = poll.total_votes()
        my_vote = poll.user_vote(current_user.id)
        options_with_pct = [
            {'label': opt, 'count': counts[i],
             'pct': round(counts[i] / total * 100) if total else 0}
            for i, opt in enumerate(poll.options)
        ]
        poll_data.append({'poll': poll, 'options': options_with_pct,
                          'total': total, 'my_vote': my_vote})
    return render_template('event.html', event=event, messages=messages,
                           poll_data=poll_data, mins=mins,
                           my_absence=my_absence, days_left=days_left, today=today)


@app.route('/events/<int:event_id>/chat', methods=['POST'])
@login_required
def post_event_message(event_id):
    Event.query.get_or_404(event_id)
    msg_type = request.form.get('msg_type', 'text')

    if msg_type == 'image':
        file = request.files.get('image')
        if not file or not file.filename or not allowed_file(file.filename):
            flash('Please select a valid image (PNG/JPG/GIF/WEBP).', 'error')
            return redirect(url_for('event_hub', event_id=event_id) + '#chat')
        url = upload_to_cloudinary(file, folder='rotaract/event_chat', resource_type='image')
        if not url:
            flash('Image upload failed. Please try again.', 'error')
            return redirect(url_for('event_hub', event_id=event_id) + '#chat')
        caption = request.form.get('content', '').strip()
        msg = EventMessage(event_id=event_id, user_id=current_user.id,
                           content=caption, msg_type='image', image_path=url)

    elif msg_type == 'location':
        if current_user.role != 'admin':
            flash('Only admin can pin locations.', 'error')
            return redirect(url_for('event_hub', event_id=event_id) + '#chat')
        loc_url = request.form.get('location_url', '').strip()
        label = request.form.get('content', '').strip() or 'View Location'
        if not loc_url:
            flash('Location URL is required.', 'error')
            return redirect(url_for('event_hub', event_id=event_id) + '#chat')
        msg = EventMessage(event_id=event_id, user_id=current_user.id,
                           content=loc_url, msg_type='location',
                           image_path=label)  # reuse image_path for label

    else:
        content = request.form.get('content', '').strip()
        if not content:
            flash('Message cannot be empty.', 'error')
            return redirect(url_for('event_hub', event_id=event_id) + '#chat')
        msg = EventMessage(event_id=event_id, user_id=current_user.id,
                           content=content, msg_type='text')

    db.session.add(msg)
    db.session.commit()
    return redirect(url_for('event_hub', event_id=event_id) + '#chat-bottom')


@app.route('/events/<int:event_id>/poll/<int:poll_id>/vote', methods=['POST'])
@login_required
def vote_poll(event_id, poll_id):
    poll = EventPoll.query.get_or_404(poll_id)
    option_index = request.form.get('option', type=int, default=-1)
    if option_index < 0 or option_index >= len(poll.options):
        flash('Invalid option.', 'error')
        return redirect(url_for('event_hub', event_id=event_id) + '#polls')
    existing = EventPollVote.query.filter_by(poll_id=poll_id, user_id=current_user.id).first()
    if existing:
        existing.option_index = option_index
    else:
        db.session.add(EventPollVote(poll_id=poll_id, user_id=current_user.id, option_index=option_index))
    db.session.commit()
    return redirect(url_for('event_hub', event_id=event_id) + '#polls')


@app.route('/admin/events/<int:event_id>/poll', methods=['POST'])
@login_required
@admin_required
def admin_create_poll(event_id):
    Event.query.get_or_404(event_id)
    question = request.form.get('question', '').strip()
    options = [o.strip() for o in request.form.getlist('options') if o.strip()]
    if not question or len(options) < 2:
        flash('Need a question and at least 2 options.', 'error')
        return redirect(url_for('event_hub', event_id=event_id) + '#polls')
    poll = EventPoll(event_id=event_id, question=question,
                     options_json=json.dumps(options), created_by_id=current_user.id)
    db.session.add(poll)
    db.session.commit()
    notify_all_members(
        f'New Poll: {question[:60]}',
        f'Vote on the poll for "{Event.query.get(event_id).title}"',
        f'/events/{event_id}#polls',
    )
    flash('Poll created.', 'success')
    return redirect(url_for('event_hub', event_id=event_id) + '#polls')


@app.route('/admin/events/<int:event_id>/minutes', methods=['POST'])
@login_required
@admin_required
def admin_add_minutes(event_id):
    Event.query.get_or_404(event_id)
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip() or None
    if not title:
        flash('Title is required.', 'error')
        return redirect(url_for('event_hub', event_id=event_id) + '#minutes')

    minutes = EventMinutes(event_id=event_id, title=title, content=content,
                           created_by_id=current_user.id)
    file = request.files.get('file')
    if file and file.filename:
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in MINUTES_EXTENSIONS:
            flash('Invalid file type. Allowed: PDF, Word, Excel, PPT, TXT.', 'error')
            return redirect(url_for('event_hub', event_id=event_id) + '#minutes')
        url = upload_to_cloudinary(file, folder='rotaract/minutes', resource_type='auto')
        if not url:
            flash('File upload failed. Please try again.', 'error')
            return redirect(url_for('event_hub', event_id=event_id) + '#minutes')
        minutes.file_path = url
        minutes.file_original_name = file.filename

    if not content and not (file and file.filename):
        flash('Add text content or upload a file.', 'error')
        return redirect(url_for('event_hub', event_id=event_id) + '#minutes')

    db.session.add(minutes)
    db.session.commit()
    flash('Minutes/report added.', 'success')
    return redirect(url_for('event_hub', event_id=event_id) + '#minutes')


@app.route('/admin/events/<int:event_id>/minutes/<int:minutes_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_minutes(event_id, minutes_id):
    m = EventMinutes.query.get_or_404(minutes_id)
    db.session.delete(m)
    db.session.commit()
    flash('Deleted.', 'success')
    return redirect(url_for('event_hub', event_id=event_id) + '#minutes')


@app.route('/admin/events/<int:event_id>/poll/<int:poll_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_poll(event_id, poll_id):
    poll = EventPoll.query.get_or_404(poll_id)
    db.session.delete(poll)
    db.session.commit()
    flash('Poll deleted.', 'success')
    return redirect(url_for('event_hub', event_id=event_id) + '#polls')


# ─── Push Notification API ───────────────────────────────────────────────────

@app.route('/api/push/vapid-public-key')
@login_required
def api_push_vapid_key():
    return jsonify({'publicKey': VAPID_PUBLIC_KEY})


@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def api_push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint', '')
    keys = data.get('keys', {})
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'error': 'Invalid subscription data'}), 400
    # Upsert: update if endpoint already exists
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        sub.user_id = current_user.id
        sub.p256dh = keys['p256dh']
        sub.auth = keys['auth']
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=keys['p256dh'],
            auth=keys['auth'],
        )
        db.session.add(sub)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def api_push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint', '')
    sub = PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user.id).first()
    if sub:
        db.session.delete(sub)
        db.session.commit()
    return jsonify({'ok': True})


# ─── Club Documents ──────────────────────────────────────────────────────────

DOCS_UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads', 'documents')


@app.route('/documents')
@login_required
def documents():
    folders = DocumentFolder.query.order_by(DocumentFolder.created_at).all()
    return render_template('documents.html', folders=folders)


@app.route('/admin/documents/folder', methods=['POST'])
@login_required
@admin_required
def admin_documents_create_folder():
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if not name:
        flash('Folder name is required.', 'error')
        return redirect(url_for('documents'))
    folder = DocumentFolder(name=name, description=description or None,
                            created_by_id=current_user.id)
    db.session.add(folder)
    db.session.commit()
    flash(f'Folder "{name}" created.', 'success')
    return redirect(url_for('documents'))


@app.route('/admin/documents/folder/<int:folder_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_documents_delete_folder(folder_id):
    folder = DocumentFolder.query.get_or_404(folder_id)
    # Files are stored on Cloudinary — no local deletion needed
    for doc in folder.documents.all():
        try:
            pass  # Cloudinary cleanup can be added here if needed
        except Exception:
            pass
    db.session.delete(folder)
    db.session.commit()
    flash(f'Folder "{folder.name}" deleted.', 'success')
    return redirect(url_for('documents'))


@app.route('/admin/documents/upload', methods=['POST'])
@login_required
@admin_required
def admin_documents_upload():
    folder_id = request.form.get('folder_id', type=int)
    title = request.form.get('title', '').strip()
    file = request.files.get('file')
    if not folder_id or not file or not file.filename:
        flash('Folder and file are required.', 'error')
        return redirect(url_for('documents'))
    folder = DocumentFolder.query.get_or_404(folder_id)
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in DOCUMENT_EXTENSIONS:
        flash('File type not allowed.', 'error')
        return redirect(url_for('documents'))
    safe_name = secure_filename(file.filename)
    url = upload_to_cloudinary(file, folder='rotaract/documents', resource_type='auto')
    if not url:
        flash('File upload failed. Please try again.', 'error')
        return redirect(url_for('documents'))
    doc = ClubDocument(
        folder_id=folder.id,
        title=title or safe_name,
        file_path=url,
        file_original_name=safe_name,
        uploaded_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.commit()
    flash(f'"{doc.title}" uploaded to {folder.name}.', 'success')
    return redirect(url_for('documents'))


@app.route('/admin/documents/<int:doc_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_documents_delete(doc_id):
    doc = ClubDocument.query.get_or_404(doc_id)
    db.session.delete(doc)
    db.session.commit()
    flash(f'"{doc.title}" deleted.', 'success')
    return redirect(url_for('documents'))


@app.route('/documents/download/<int:doc_id>')
@login_required
def documents_download(doc_id):
    doc = ClubDocument.query.get_or_404(doc_id)
    return redirect(doc.file_path)


# ─── DB Init & Seed ──────────────────────────────────────────────────────────

def migrate_schema():
    """Add new columns to existing tables without dropping data."""
    from sqlalchemy import text, inspect
    inspector = inspect(db.engine)
    existing_tables = inspector.get_table_names()
    with db.engine.connect() as conn:
        user_cols = [c['name'] for c in inspector.get_columns('user')] if 'user' in existing_tables else []
        if 'profile_picture' not in user_cols:
            conn.execute(text('ALTER TABLE user ADD COLUMN profile_picture VARCHAR(200)'))
            conn.commit()
            print('  ✔ Added profile_picture column')
        if 'fees_status' not in user_cols:
            conn.execute(text("ALTER TABLE user ADD COLUMN fees_status VARCHAR(20) DEFAULT 'unpaid'"))
            conn.commit()
            print('  ✔ Added fees_status column')
        if 'fees_amount' not in user_cols:
            conn.execute(text('ALTER TABLE user ADD COLUMN fees_amount INTEGER DEFAULT 0'))
            conn.commit()
            print('  ✔ Added fees_amount column')
        if 'fees_paid_amount' not in user_cols:
            conn.execute(text('ALTER TABLE user ADD COLUMN fees_paid_amount INTEGER DEFAULT 0'))
            conn.commit()
            print('  ✔ Added fees_paid_amount column')
        if 'fees_paid_date' not in user_cols:
            conn.execute(text('ALTER TABLE user ADD COLUMN fees_paid_date DATE'))
            conn.commit()
            print('  ✔ Added fees_paid_date column')
    # Create any missing tables (push_subscription, event_message, event_poll, etc.)
    db.create_all()
    print('  ✔ Ensured all tables exist')


def seed_database():
    if User.query.first():
        return
    print('Seeding database with sample data...')
    admin = User(name='Rtn. President', email='admin@rotaract-palghar.org',
                 role='admin', position='President')
    admin.set_password('admin123')
    db.session.add(admin)
    members_data = [
        ('Aarav Shah', 'aarav@example.com', 'Vice President'),
        ('Priya Mehta', 'priya@example.com', 'Secretary'),
        ('Rohan Desai', 'rohan@example.com', 'Treasurer'),
        ('Sneha Patil', 'sneha@example.com', 'Director - Community Service'),
        ('Karan Joshi', 'karan@example.com', 'Director - Projects'),
        ('Neha Sharma', 'neha@example.com', 'Sergeant at Arms'),
        ('Vikram Nair', 'vikram@example.com', 'Member'),
        ('Ananya Iyer', 'ananya@example.com', 'Member'),
        ('Rahul Gupta', 'rahul@example.com', 'Member'),
        ('Pooja Singh', 'pooja@example.com', 'Member'),
    ]
    members = []
    for name, email, position in members_data:
        u = User(name=name, email=email, position=position)
        u.set_password('member123')
        db.session.add(u)
        members.append(u)
    metrics_data = [
        ('Event Attendance', 'Points for attending club meetings and events', '📅', '#0072CE'),
        ('Community Service', 'Points for community service hours', '🤝', '#10b981'),
        ('Project Leadership', 'Points for leading or co-leading projects', '🚀', '#F7A81B'),
        ('Fundraising', 'Points for fundraising contributions', '💰', '#8b5cf6'),
        ('Training & Development', 'Points for attending district/zone training', '📚', '#ef4444'),
        ('Social Media Engagement', 'Points for club social media participation', '📱', '#f59e0b'),
        ('Membership Recruitment', 'Points for bringing new members', '👥', '#06b6d4'),
    ]
    metrics = []
    for name, desc, icon, color in metrics_data:
        m = Metric(name=name, description=desc, icon=icon, color=color, max_points=200)
        db.session.add(m)
        metrics.append(m)
    db.session.flush()
    now = datetime.utcnow()
    events_data = [
        ('Weekly Assembly Meeting', 'meeting', now + timedelta(days=3), 'Club House, Palghar', 10),
        ('Blood Donation Drive', 'project', now + timedelta(days=7), 'Palghar Civil Hospital', 50),
        ('District Assembly', 'event', now + timedelta(days=14), 'Thane', 30),
        ('Annual Gala Night', 'social', now + timedelta(days=21), 'Hotel Grand, Palghar', 20),
        ('Tree Plantation Drive', 'project', now + timedelta(days=10), 'Palghar Beach', 40),
    ]
    for title, etype, edate, location, pts in events_data:
        db.session.add(Event(title=title, event_type=etype, date=edate,
                             location=location, points_for_attendance=pts))
    db.session.flush()
    import random
    random.seed(42)
    today = date.today()
    for member in members:
        for _ in range(random.randint(5, 15)):
            metric = random.choice(metrics)
            pts = random.choice([10, 15, 20, 25, 30, 40, 50, -10])
            entry = PointEntry(
                member_id=member.id, metric_id=metric.id, points=pts,
                note=f'Sample entry for {metric.name}',
                date=today - timedelta(days=random.randint(0, 90)),
                added_by_id=admin.id,
            )
            db.session.add(entry)
    db.session.commit()
    print('Database seeded. Admin: admin@rotaract-palghar.org / admin123')
    print('Member: aarav@example.com / member123')


# ─── Startup initialisation (runs at import time, before gunicorn serves) ────
# db.create_all() is idempotent — safe to call on every deploy/restart.
with app.app_context():
    db.create_all()
    try:
        migrate_schema()
    except Exception as _e:
        print(f'migrate_schema skipped: {_e}')
    if not User.query.first():
        _admin = User(
            name='Admin',
            email='admin@rotaract-palghar.org',
            role='admin',
            position='President',
        )
        _admin.set_password('admin123')
        db.session.add(_admin)
        db.session.commit()
        print('Default admin created: admin@rotaract-palghar.org / admin123')


if __name__ == '__main__':
    with app.app_context():
        seed_database()
    app.run(debug=True, port=8082)
