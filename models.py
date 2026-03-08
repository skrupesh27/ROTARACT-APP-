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
