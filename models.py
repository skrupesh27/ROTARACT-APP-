import json as _json
import os
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date

db = SQLAlchemy()


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='member')  # 'admin' or 'member'
    position = db.Column(db.String(100), default='Member')
    phone = db.Column(db.String(20))
    profile_picture = db.Column(db.String(200))  # filename in uploads/
    joined_date = db.Column(db.Date, default=date.today)
    is_active_member = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Fees tracking
    fees_status = db.Column(db.String(20), default='unpaid')   # unpaid / partial / paid
    fees_amount = db.Column(db.Integer, default=0)             # total amount due
    fees_paid_amount = db.Column(db.Integer, default=0)        # amount paid so far
    fees_paid_date = db.Column(db.Date)                        # date of last payment

    point_entries = db.relationship(
        'PointEntry', foreign_keys='PointEntry.member_id',
        backref='member', lazy='dynamic'
    )
    event_attendances = db.relationship(
        'EventAttendance', foreign_keys='EventAttendance.member_id',
        backref='member', lazy='dynamic'
    )
    absence_requests = db.relationship(
        'AbsenceRequest', foreign_keys='AbsenceRequest.member_id',
        backref='member', lazy='dynamic'
    )
    disputes = db.relationship(
        'PointDispute', foreign_keys='PointDispute.member_id',
        backref='member', lazy='dynamic'
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def total_points(self):
        return db.session.query(db.func.sum(PointEntry.points))\
            .filter(PointEntry.member_id == self.id).scalar() or 0

    @property
    def initials(self):
        parts = self.name.split()
        return ''.join(p[0] for p in parts[:2]).upper()

    @property
    def points_this_month(self):
        today = date.today()
        start = date(today.year, today.month, 1)
        return db.session.query(db.func.sum(PointEntry.points))\
            .filter(PointEntry.member_id == self.id, PointEntry.date >= start).scalar() or 0

    @property
    def events_attended(self):
        return self.event_attendances.filter_by(attended=True).count()

    def points_by_metric(self):
        results = db.session.query(Metric.name, Metric.color, db.func.sum(PointEntry.points))\
            .join(PointEntry, PointEntry.metric_id == Metric.id)\
            .filter(PointEntry.member_id == self.id)\
            .group_by(Metric.id).all()
        return [{'name': r[0], 'color': r[1], 'points': r[2] or 0} for r in results]

    @property
    def fees_color(self):
        return {'paid': '#10b981', 'partial': '#F7A81B', 'unpaid': '#ef4444'}.get(self.fees_status, '#6b7280')

    @property
    def avatar_url(self):
        if self.profile_picture:
            return f'/static/uploads/avatars/{self.profile_picture}'
        return None

    def __repr__(self):
        return f'<User {self.name}>'


class Metric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    icon = db.Column(db.String(10), default='⭐')
    color = db.Column(db.String(20), default='#0072CE')
    max_points = db.Column(db.Integer, default=100)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship('PointEntry', backref='metric', lazy='dynamic')

    @property
    def total_distributed(self):
        return db.session.query(db.func.sum(PointEntry.points))\
            .filter(PointEntry.metric_id == self.id).scalar() or 0

    def __repr__(self):
        return f'<Metric {self.name}>'


class PointEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    metric_id = db.Column(db.Integer, db.ForeignKey('metric.id'), nullable=False)
    points = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(300))
    date = db.Column(db.Date, default=date.today)
    added_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    added_by = db.relationship('User', foreign_keys=[added_by_id])

    def __repr__(self):
        return f'<PointEntry {self.member_id} +{self.points}>'


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    event_type = db.Column(db.String(50), default='meeting')  # meeting/project/event/social
    date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime)
    description = db.Column(db.Text)
    location = db.Column(db.String(200))
    google_calendar_id = db.Column(db.String(200))
    points_for_attendance = db.Column(db.Integer, default=10)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attendances = db.relationship('EventAttendance', backref='event', lazy='dynamic',
                                  cascade='all, delete-orphan')
    event_absence_requests = db.relationship('AbsenceRequest', backref='event',
                                             lazy='dynamic', cascade='all, delete-orphan')
    messages = db.relationship('EventMessage', backref='event', lazy='dynamic',
                               cascade='all, delete-orphan')
    polls = db.relationship('EventPoll', backref='event', lazy='dynamic',
                            cascade='all, delete-orphan')
    minutes_list = db.relationship('EventMinutes', backref='event', lazy='dynamic',
                                   cascade='all, delete-orphan')

    @property
    def attendance_count(self):
        return self.attendances.filter_by(attended=True).count()

    @property
    def type_color(self):
        colors = {
            'meeting': '#0072CE',
            'project': '#F7A81B',
            'event': '#10b981',
            'social': '#8b5cf6'
        }
        return colors.get(self.event_type, '#6b7280')

    def __repr__(self):
        return f'<Event {self.title}>'


class EventAttendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    attended = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<EventAttendance event={self.event_id} member={self.member_id}>'


class AbsenceRequest(db.Model):
    """Member submits a request to be excused from a specific event."""
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending / approved / rejected
    admin_note = db.Column(db.Text)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id])

    @property
    def status_color(self):
        return {'pending': '#F7A81B', 'approved': '#10b981', 'rejected': '#ef4444'}.get(self.status, '#6b7280')

    def __repr__(self):
        return f'<AbsenceRequest member={self.member_id} event={self.event_id} status={self.status}>'


class PointDispute(db.Model):
    """Member raises a complaint or question about points."""
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    metric_name = db.Column(db.String(100))   # optional: which metric they're questioning
    event_name = db.Column(db.String(200))    # optional: which event
    status = db.Column(db.String(20), default='open')  # open / resolved / rejected
    admin_response = db.Column(db.Text)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    resolved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    resolved_by = db.relationship('User', foreign_keys=[resolved_by_id])

    @property
    def status_color(self):
        return {'open': '#F7A81B', 'resolved': '#10b981', 'rejected': '#ef4444'}.get(self.status, '#6b7280')

    def __repr__(self):
        return f'<PointDispute member={self.member_id} status={self.status}>'


class PushSubscription(db.Model):
    """Stores Web Push API subscriptions per user device."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)   # public key
    auth = db.Column(db.Text, nullable=False)       # auth secret
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('push_subscriptions', lazy='dynamic'))

    def to_dict(self):
        return {
            'endpoint': self.endpoint,
            'keys': {'p256dh': self.p256dh, 'auth': self.auth},
        }

    def __repr__(self):
        return f'<PushSubscription user={self.user_id}>'


class EventMessage(db.Model):
    """Chat message in an event discussion."""
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    msg_type = db.Column(db.String(20), default='text')  # text / image / location
    image_path = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return f'<EventMessage event={self.event_id} user={self.user_id}>'


class EventPoll(db.Model):
    """Admin-created poll for an event."""
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    question = db.Column(db.String(500), nullable=False)
    options_json = db.Column(db.Text, nullable=False)   # JSON list of strings
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id])
    poll_votes = db.relationship('EventPollVote', backref='poll', lazy='dynamic',
                                 cascade='all, delete-orphan')

    @property
    def options(self):
        return _json.loads(self.options_json)

    def vote_counts(self):
        counts = [0] * len(self.options)
        for v in self.poll_votes.all():
            if 0 <= v.option_index < len(counts):
                counts[v.option_index] += 1
        return counts

    def total_votes(self):
        return self.poll_votes.count()

    def user_vote(self, user_id):
        v = self.poll_votes.filter_by(user_id=user_id).first()
        return v.option_index if v else None

    def __repr__(self):
        return f'<EventPoll event={self.event_id}>'


class EventPollVote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('event_poll.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    option_index = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return f'<EventPollVote poll={self.poll_id} user={self.user_id}>'


class EventMinutes(db.Model):
    """Minutes of meeting or project report."""
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text)
    file_path = db.Column(db.String(200))
    file_original_name = db.Column(db.String(200))
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    @property
    def file_ext(self):
        if self.file_original_name and '.' in self.file_original_name:
            return self.file_original_name.rsplit('.', 1)[-1].lower()
        return ''

    @property
    def file_icon(self):
        return {'pdf': '📄', 'doc': '📝', 'docx': '📝', 'xls': '📊',
                'xlsx': '📊', 'ppt': '📑', 'pptx': '📑', 'txt': '📋'}.get(self.file_ext, '📎')

    def __repr__(self):
        return f'<EventMinutes event={self.event_id}>'


class DocumentFolder(db.Model):
    """Folder that holds club documents."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(400))
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id])
    documents = db.relationship('ClubDocument', backref='folder', lazy='dynamic',
                                cascade='all, delete-orphan')

    def __repr__(self):
        return f'<DocumentFolder {self.name}>'


class ClubDocument(db.Model):
    """A file uploaded to a club document folder."""
    id = db.Column(db.Integer, primary_key=True)
    folder_id = db.Column(db.Integer, db.ForeignKey('document_folder.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    file_path = db.Column(db.String(300), nullable=False)       # path under static/uploads/documents/
    file_original_name = db.Column(db.String(300), nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploaded_by = db.relationship('User', foreign_keys=[uploaded_by_id])

    @property
    def file_ext(self):
        if '.' in self.file_original_name:
            return self.file_original_name.rsplit('.', 1)[-1].lower()
        return ''

    @property
    def file_icon(self):
        icons = {
            'pdf': '📄', 'doc': '📝', 'docx': '📝',
            'xls': '📊', 'xlsx': '📊',
            'ppt': '📑', 'pptx': '📑',
            'txt': '📋', 'csv': '📋',
            'png': '🖼️', 'jpg': '🖼️', 'jpeg': '🖼️', 'gif': '🖼️',
            'zip': '🗜️', 'rar': '🗜️',
        }
        return icons.get(self.file_ext, '📎')

    @property
    def file_size_kb(self):
        try:
            from flask import current_app
            full = os.path.join(current_app.static_folder, 'uploads', 'documents', self.file_path)
            return round(os.path.getsize(full) / 1024, 1)
        except Exception:
            return 0

    def __repr__(self):
        return f'<ClubDocument {self.title}>'
