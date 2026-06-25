"""
One-time script to create club members.
Run with: python create_members.py
"""
import os
from app import app
from models import db, User

members = [
    ('Prathamesh Save',   'rtrprathameshsave@gmail.com',  'HRD and Partner in Service', 'Prathamesh123'),
    ('Nancy Rathod',      'nancyrathod99@gmail.com',       'Vice President',             'Nancy123'),
    ('Shruti Singh',      'iitzshru@gmail.com',            'Joint Secretary & Digital Communication', 'Shruti123'),
    ('Ameer Shaikh',      'rtrameershaikh@gmail.com',      'Treasurer',                  'Ameer123'),
    ('Kavish Vyas',       'kavishvyas3131@gmail.com',      'Entrepreneurs Development',  'Kavish123'),
    ('Vikaas Gupta',      'vikasvijaygupta8282@gmail.com', 'Social Media',               'Vikaas123'),
    ('Dev Dhoot',         'dev15dhoot@gmail.com',          'Club Editor',                'Dev123'),
    ('Yash Patil',        'yashsp2209204@gmail.com',       'Community Service',          'Yash123'),
    ('Jinal Jain',        'rtrjinal0510@gmail.com',        'Secretary',                  'Jinal123'),
    ('Farha Qureshi',     'rtrfarhaq49110@gmail.com',      'Sergeant at Arms',           'Farha123'),
    ('Shrey Shaparia',    'rtr.shreys@gmail.com',          'Club Advisor',               'Shrey123'),
    ('Rashi Kewat',       'rashikewat22@gmail.com',        'CSR Director',               'Rashi123'),
    ('Dev Sarkar',        'devsarkar1132005@gmail.com',    'PR & Marketing',             'Dev123'),
    ('Krishna Patel',     'kc02patel@gmail.com',           'GBM',                        'Krishna123'),
    ('Garvita Sharma',    'garvitasharma790@gmail.com',    'Membership',                 'Garvita123'),
    ('Meghana Kamath',    'meghanakamath2004@gmail.com',   'GBM',                        'Meghana123'),
    ('Sanu Paswan',       'sanurajpaswan@gmail.com',       'Sports Director',            'Sanu123'),
    ('Niyati Kini',       'niyatikini0227@gmail.com',      'GBM',                        'Niyati123'),
    ('Devank Mhatre',     'devankmhatre4624@gmail.com',    'GBM',                        'Devank123'),
    ('Harshita Gharat',   'harshitagharat2@gmail.com',     'GBM',                        'Harshita123'),
    ('Prahlad Rawal',     'prahladrawal4@gmail.com',       'Professional Growth',        'Prahlad123'),
    ('Samruddhi Patil',   'samruddhipatil1116@gmail.com',  'Club Service',               'Samruddhi123'),
    ('Gargi Patil',       'rtrgargi16@gmail.com',          'IPP',                        'Gargi123'),
]

with app.app_context():
    created, skipped = [], []
    for name, email, position, password in members:
        if User.query.filter_by(email=email.lower()).first():
            skipped.append(email)
            continue
        u = User(name=name, email=email.lower(), position=position, role='member')
        u.set_password(password)
        db.session.add(u)
        created.append((name, email, password))
    db.session.commit()

    print(f'\n✅ Created {len(created)} members:')
    for name, email, password in created:
        print(f'   {name:<22} {email:<40} pw: {password}')
    if skipped:
        print(f'\n⚠️  Skipped {len(skipped)} (already exist): {", ".join(skipped)}')
    print()
