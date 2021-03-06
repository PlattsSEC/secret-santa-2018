from flask import Flask, render_template, request, redirect, jsonify, url_for
from flask_wtf.csrf import CSRFProtect
from datetime import datetime, date
from models import (
    db,
    Group,
    User,
    Pair,
    PairCreationStatus,
    GroupsAndUsersAssociation,
    GroupPairReveals,
    EmailInvite,
    PasswordReset,
    all_admin_materialized_view,
    all_latest_pairs_view,
)
from serializers import ma
from sqlalchemy import func, select
from celery import Celery
from flask_mail import Mail
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_bootstrap import Bootstrap
from forms import (
    SignUpForm,
    LoginForm,
    ProfileEditForm,
    InviteUserToGroupForm,
    CreateGroupForm,
    ChangePasswordForm,
    CreatePairsForm,
    ResetPasswordForm,
    LeaveGroupForm,
)

import os
import admin
import hashlib
from datetime import datetime
from random import choices
from sql import AdvisoryLock
import maya

app = Flask(__name__)
app.config.update(
    dict(
        DEBUG=False if os.environ.get("ENV") == "production" else True,
        MAIL_SERVER="smtp.gmail.com",
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USE_SSL=False,
        MAIL_USERNAME=f"{os.environ.get('MAIL_USERNAME')}@gmail.com",
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL"),
        SQLALCHEMY_TRACK_MODIFICATIONS=True,
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        CSRF_ENABLED=True,
    )
)

csrf = CSRFProtect(app)

Bootstrap(app)
db.init_app(app)
ma.init_app(app)
mail = Mail(app)
admin.register(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
csrf.init_app(app)

celery = Celery("tasks", broker=os.environ.get("CELERY_BROKER_URL"))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


# Routes


@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    form = ResetPasswordForm()
    if form.validate_on_submit():
        email = form.email.data
        user = User.query.filter_by(email=email).first()
        if user:
            currently_running_reset_attempt = (
                PasswordReset.query.with_entities(func.max(PasswordReset.started_at))
                .filter_by(user_id=user.id, status="resetting")
                .first()[0]
            )
            if currently_running_reset_attempt:
                message = "Already processing recent password reset"
                return render_template(
                    "reset_password.html", form=form, message=message
                )

            password_reset_attempts = PasswordReset.query.filter_by(
                user_id=user.id
            ).all()
            if len(password_reset_attempts) < 3:
                with AdvisoryLock(
                    engine=db.engine, lock_key=user.email
                ).grab_lock() as locked_session:
                    lock, session = locked_session
                    if lock:
                        new_attempt = PasswordReset(
                            user_id=user.id,
                            started_at=datetime.now(),
                            status="resetting",
                        )

                        session.add(new_attempt)

                    else:
                        message = "Too many reset attempts at the same time!"
                        return render_template(
                            "/reset_password", form=form, message=message
                        )
                # Send task to celery ONLY AFTER we have
                # 1. Committed the new row to db
                # 2. Released the lock

                celery.send_task("user.reset_password", (user.email,))

                message = f"A temporary password will be sent to {user.email} shortly"
                return render_template(
                    "reset_password.html", form=form, message=message
                )
            else:
                last_reset_attempt_date = (
                    PasswordReset.query.with_entities(
                        func.max(PasswordReset.started_at)
                    )
                    .filter_by(user_id=user.id)
                    .first()[0]
                )
                today = datetime.now()

                if (today - last_reset_attempt_date).days < 30:
                    message = "Too many reset attempts recently. Check back again after a few days"
                    return render_template(
                        "reset_password.html", form=form, message=message
                    )
                else:

                    for attempt in password_reset_attempts:
                        attempt.delete_from_db(db)

                    with AdvisoryLock(
                        engine=db.engine, lock_key=user.email
                    ).grab_lock() as locked_session:
                        lock, session = locked_session
                        if lock:
                            new_attempt = PasswordReset(
                                user_id=user.id,
                                started_at=datetime.now(),
                                status="resetting",
                            )

                            session.add(new_attempt)
                        else:
                            message = "Too many reset attempts at the same time!"
                            return render_template(
                                "reset_password", form=form, message=message
                            )

                    # Send task to celery ONLY AFTER we have
                    # 1. Committed the new row to db
                    # 2. Released the lock

                    celery.send_task("user.reset_password", (user.email,))

                    message = (
                        f"A temporary password will be sent to {user.email} shortly"
                    )
                    return render_template(
                        "reset_password.html", form=form, message=message
                    )

        message = f"No user with the email {form.email.data} was found"
        return render_template("reset_password.html", form=form, message=message)
    return render_template("reset_password.html", form=form)


@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.verify_hash(form.current_password.data, current_user.password):
            current_user.set_password(form.new_password.data)
            current_user.save_to_db(db)

            return redirect("/profile")
    return render_template("change_password.html", form=form)


@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    form = ProfileEditForm()
    if form.validate_on_submit():
        hint = form.hint.data
        address = form.address.data
        first_name = form.first_name.data
        last_name = form.last_name.data
        current_user.hint = hint
        current_user.address = address
        current_user.first_name = first_name
        current_user.last_name = last_name

        current_user.save_to_db(db)

        return redirect("/profile")
    hint = current_user.hint
    address = current_user.address
    return render_template("edit_profile.html", form=form, hint=hint, address=address)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    return render_template("profile.html")


@app.route("/santa", methods=["GET", "POST"])
@login_required
def santa():
    all_groups = [
        i.group
        for i in GroupsAndUsersAssociation.query.filter_by(
            user_id=current_user.id
        ).all()
    ]
    groups_that_can_be_revealed = [
        group for group in all_groups if group.reveal_latest_pairs
    ]
    return render_template("santa.html", groups=groups_that_can_be_revealed)


@app.route("/groups", methods=["GET", "POST"])
@login_required
def group():
    # Forms
    create_group_form = CreateGroupForm()
    leave_group_form = LeaveGroupForm()
    invite_user_to_group_form = InviteUserToGroupForm()
    create_pairs_form = CreatePairsForm()

    # Args
    message = request.args.get("message")

    if request.method == "POST":
        if (
            create_group_form.submit_create_group_form.data
            and create_group_form.validate()
        ):
            group_name = create_group_form.name.data
            group = Group.query.filter_by(name=group_name).first()
            if group:
                return redirect(
                    url_for(
                        ".group",
                        message=f"Group with the name {group_name} already exists",
                    )
                )
            new_group = Group(name=group_name)
            new_group.save_to_db(db)
            new_group_assoc = GroupsAndUsersAssociation(
                group=new_group, user=current_user, group_admin=True
            )
            new_group_assoc.save_to_db(db)

            # TODO: @prithajnath
            # We shouldn't have to do this here. This should be taken care of by the right SQLALchemy hook
            all_admin_materialized_view.refresh()

            return redirect(url_for(".group", group_id=new_group.id))

        if (
            leave_group_form.submit_leave_group_form.data
            and leave_group_form.validate()
        ):
            group_name = leave_group_form.group_name.data
            print(leave_group_form)
            print(group_name)
            group = Group.query.filter_by(name=group_name).first()

            group_assoc_with_user = GroupsAndUsersAssociation.query.filter_by(
                group=group, user=current_user
            ).first()
            group_assoc_with_user.delete_from_db(db)
            return redirect(url_for(".group"))

        if (
            create_pairs_form.submit_create_pairs_form.data
            and create_pairs_form.validate()
        ):
            print(create_pairs_form.submit_create_pairs_form.data)
            group_id = request.args.get("group_id")
            print(f"Creating pairs for {group_id}")
            group = Group.query.filter_by(id=group_id).first()

            currently_running_creation_attempt = PairCreationStatus.query.filter_by(
                group_id=group.id, status="creating"
            ).first()

            if currently_running_creation_attempt:
                message = "Pair creation already in progress"
                return redirect(url_for(".group", message=message, group_id=group_id))

            pair_latest_timestamp = Pair.query.with_entities(
                func.max(Pair.timestamp)
            ).filter_by(group_id=group_id).first()[0] or datetime(1970, 1, 1)

            today = datetime.now()

            delta = today - pair_latest_timestamp
            if delta.days > 1:
                if group.is_admin(current_user) and current_user.is_authenticated:

                    with AdvisoryLock(
                        engine=db.engine, lock_key=group.name
                    ).grab_lock() as locked_session:
                        lock, session = locked_session
                        if lock:
                            # NOTE: Can't pass SQLAlchemy objects like group* and current_user** here because
                            # they are attached to a different session
                            creation_attempt = PairCreationStatus(
                                group_id=group.id,  # *
                                started_at=datetime.now(),
                                initiator_id=current_user.id,  # **
                                status="creating",
                            )

                            session.add(creation_attempt)
                        else:
                            message = (
                                "Too many attempts to create pairs at the same time!"
                            )
                            return redirect(
                                url_for(".group", message=message, group_id=group_id)
                            )

                    # Again, only queue messages after lock has been released
                    task = celery.send_task("pair.create", (group_id,))

                    if task.status == "PENDING":
                        timestamp = maya.MayaDT.from_datetime(datetime.now())
                        message = f"Pair creation has been initiated at {timestamp.__str__()}. Sit tight!"
                        return redirect(
                            url_for(".group", message=message, group_id=group_id)
                        )
            else:
                message = "This group is in cooldown (Pairs were created recently). Please try again in a day"
                return redirect(url_for(".group", message=message, group_id=group_id))

        if (
            invite_user_to_group_form.submit_invite_form.data
            and invite_user_to_group_form.validate()
        ):
            group_id = request.args.get("group_id")
            group = Group.query.filter_by(id=group_id).first()
            user = User.query.filter_by(
                email=invite_user_to_group_form.email.data
            ).first()
            if user:
                new_group_assoc = GroupsAndUsersAssociation(
                    group_id=group.id, user_id=user.id
                )
                new_group_assoc.save_to_db(db)
                return redirect(url_for(".group", group_id=group.id))
            else:

                new_invite = EmailInvite(
                    timestamp=datetime.now(),
                    invited_email=invite_user_to_group_form.email.data,
                    group_id=group.id,
                )

                new_invite.save_to_db(db)

                to_email = new_invite.invited_email
                admin_first_name = current_user.first_name
                group_name = group.name
                task = celery.send_task(
                    "user.invite", (to_email, admin_first_name, group_name)
                )

                if task.status == "PENDING":
                    return redirect(
                        url_for(
                            ".group",
                            message=f"{invite_user_to_group_form.email.data} has been invited to create an account and join this group!",
                            group_id=group_id,
                            create_pairs_form=create_pairs_form,
                            form=invite_user_to_group_form,
                        )
                    )

    group_id = request.args.get("group_id")
    message = request.args.get("message")
    alert = request.args.get("alert")
    group = Group.query.filter_by(id=group_id).first()

    if group:
        if GroupsAndUsersAssociation.query.filter_by(
            group_id=group.id, user_id=current_user.id
        ).first():
            return render_template(
                "group.html",
                create_pairs_form=create_pairs_form,
                invite_user_to_group_form=invite_user_to_group_form,
                group=group,
                message=message,
                alert=alert,
            )

    groups = [i.group for i in current_user.groups]

    return render_template(
        "my_groups.html",
        groups=groups,
        create_group_form=create_group_form,
        leave_group_form=leave_group_form,
        message=message,
        alert=alert,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect("/profile")
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user:
            if user.verify_hash(form.password.data, user.password):
                login_user(user)
                if user.admin:
                    return redirect("/admin")
                else:
                    return redirect("/profile")
            else:
                return redirect(
                    url_for(".login", message=f"Please check your password")
                )
        else:
            return redirect(
                url_for(
                    ".login",
                    message=f"Couldn't find user with username {form.username.data}",
                )
            )
    message = request.args.get("message")

    return render_template("login.html", message=message, form=form)


@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    form = LoginForm()
    return render_template("login.html", form=form)


@app.route("/register", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect("/profile")

    form = SignUpForm()
    if form.validate_on_submit():
        username = User.query.filter_by(username=form.username.data).first()
        email = User.query.filter_by(email=form.email.data).first()
        if not username and not email:
            user = User(
                username=form.username.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                password=form.password.data,
                email=form.email.data,
            )

            user.save_to_db(db)
            login_user(user)

            invites = EmailInvite.query.filter_by(invited_email=user.email)
            for invite in invites:
                new_group_assoc = GroupsAndUsersAssociation(
                    group_id=invite.group_id, user_id=user.id
                )
                new_group_assoc.save_to_db(db)

                invite.delete_from_db(db)
            else:
                return redirect("/groups")
        else:
            return redirect(
                url_for(
                    ".index", alert="User with that email or username already exists"
                )
            )


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect("/profile")
    form = SignUpForm()
    alert = request.args.get("alert")
    return render_template("index.html", alert=alert, form=form)


# JSON endpoints


@app.route("/reveal_toggle", methods=["POST"])
def reveal_group_santas():
    group_id = request.json.get("group_id")
    action = request.json.get("action")
    group = Group.query.filter_by(id=group_id).first()
    if group:
        with AdvisoryLock(
            engine=db.engine, lock_key=group.name
        ).grab_lock() as locked_session:
            lock, session = locked_session
            if lock:
                group_in_locked_session = (
                    session.query(Group).filter_by(id=group_id).first()
                )
                timestamp = datetime.now()
                group_reveal_history = GroupPairReveals(
                    group_id=group_id, user_id=current_user.id, timestamp=timestamp
                )
                group_in_locked_session.reveal_latest_pairs = bool(int(action))
                session.add(group_in_locked_session)
                session.add(group_reveal_history)
            else:
                return jsonify(result="too many concurrent attemps!")

        return jsonify(result=action)
    return 404


@app.route("/reveal_secret_santa", methods=["GET"])
def reveal_secret_santa():
    group_name = request.args.get("group_name")
    secret_santa = all_latest_pairs_view.query.filter_by(
        group_name=group_name, receiver_id=current_user.id
    ).first()

    group = Group.query.filter_by(name=group_name).first()
    users = [i.user.username for i in group.users]
    return jsonify(username=secret_santa.username, randos=choices(users, k=10))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, use_reloader=True)
