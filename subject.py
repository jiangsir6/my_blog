# encoding=utf-8
import os
import datetime
from datetime import timedelta
import json
import time
import traceback
import zipfile
import app.common.logger_handler

from cStringIO import StringIO

import re
import xlrd
import zipstream
from flask import Blueprint, render_template, g, request, send_file
from flask import Response, stream_with_context
from flask.ext.login import login_required
from sqlalchemy import or_, and_, select, outerjoin, desc

from app.common.constants import Gender, SubjectType, VisitorPurpose, AccountPermission, VisitorInviteDays, ImportPhotoType
from app.common.error_code import ErrorCode
from app.common.json_builder import success_result, error_result
from app.common.logger import logger
from app.models import Screen
from app.common.db_helper import fast_pagination
from app.common.view_helper import get_pagination,page_format
from app.common.utility import get_str_param_check_length, is_mail
from app.common.permission import permission_required_page
from app.common.view_helper import update_company_data_version, create_user_photo, get_subject_list,\
    create_user_avatar, get_subject_list_query, check_upload_image
from app.foundation import db, storage, oss, search, redis
from app.models import Photo, Subject, DisplayDevice, PhotoAlternative, Task
from app.common.tasks import delete_subjects
from config import SUBJECT_BIND_CAMERA_COUNT
from app.common.logger_handler import log_Operation
from app.common.handle_code import *

subject_blueprint = Blueprint('subject', __name__, template_folder='templates')


@subject_blueprint.route('/subject/employee')

@login_required
@permission_required_page(AccountPermission.ADD_EMPLOYEE)
def subject_employee():
    gender = Gender.state_mapping
    return render_template('page/subject/index.html', subject_type_options=SubjectType.get_select_options(),
                           gender=gender, category='employee', subject_type=SubjectType.TYPE_EMPLOYEE)


@subject_blueprint.route('/subject/visitor')
@login_required
@permission_required_page(AccountPermission.ADD_VISITOR)
def subject_visitor():
    gender = Gender.state_mapping
    return render_template('page/subject/index.html', subject_type_options=SubjectType.get_select_options(),
                           gender=gender, category='visitor', subject_type=SubjectType.TYPE_VISITOR,
                           purpose=VisitorPurpose.state_mapping)

@subject_blueprint.route('/subject/yellowlist')
@login_required
@permission_required_page(AccountPermission.ADD_YELLOWLIST)
def subject_yellowlist():
    gender = Gender.state_mapping
    return render_template('page/subject/index.html', subject_type_options=SubjectType.get_select_options(),
                           gender=gender, category='yellowlist', subject_type=SubjectType.TYPE_YELLOWLIST)


@subject_blueprint.route('/subject/list')
@login_required
def subject_list():
    company = g.user.company
    params = request.args
    ret = get_subject_list(company, params)
    if ret[0] is None:
        return error_result(ret[1])
    return success_result(ret[0], ret[1])


@subject_blueprint.route('/subject/all')
@login_required
def subject_all():
    # TODO delete this function 
    if not g.user.has_permission(AccountPermission.ADD_EMPLOYEE):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)
    # subjects = db.session.query(Subject.id, Subject.real_name, Subject.department, Subject.title, Subject.avatar, Photo.url).outerjoin(
    # Photo, Subject.id == Photo.subject_id).filter(Subject.company_id == g.user.company.id, Subject.subject_type == SubjectType.TYPE_EMPLOYEE)

    params = request.get_json() or request.args
    current, size = get_pagination(params)
    filterstr = params.get('filterstr')
    if filterstr:
        query = db.session.query(Subject.id, Subject.real_name, Subject.department, Subject.title, Subject.avatar,
                                 Photo.url).outerjoin(
            Photo, Subject.id == Photo.subject_id).filter(Subject.company_id == g.user.company.id,
                                                          Subject.subject_type == SubjectType.TYPE_EMPLOYEE, \
                                                          or_(Subject.department.like("%{}%".format(filterstr)),\
                                                          Subject.real_name.like("%{}%".format(filterstr)))).order_by(
            desc(Subject.real_name))
    else:
        query = db.session.query(Subject.id, Subject.real_name, Subject.department, Subject.title, Subject.avatar,
                                 Photo.url).outerjoin(
            Photo, Subject.id == Photo.subject_id).filter(Subject.company_id == g.user.company.id,
                                                          Subject.subject_type == SubjectType.TYPE_EMPLOYEE).order_by(
            desc(Subject.real_name))
    try:
        pagination = fast_pagination(query, current, per_page=SUBJECT_BIND_CAMERA_COUNT)
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    subjects = pagination.items

    uniqueSubjects = []
    lastSubject = None
    for subject in subjects:
        if lastSubject is None or subject[0] != lastSubject[0]:
            lastSubject = list(subject)
            uniqueSubjects.append(lastSubject)
        if not lastSubject[4] and subject[5]:
            lastSubject[4] = subject[5]

    ret = {}

    for subject in uniqueSubjects:
        ret[subject[0]] = {
            "id": subject[0],
            "name": subject[1],
            "department": subject[2],
            "title": subject[3],
            "avatar": storage.get_url(subject[4])
        }
    return success_result(ret,page_format(pagination))


def _update_photos(subject, photos):
    if photos is None:
        return False
    photos_exist = set([photo.id for photo in subject.photos])
    photos = set(photos)
    diff = False
    if photos_exist - photos != set():
        diff = True
        useless_photos = Photo.query.filter(Photo.id.in_(photos_exist - photos)).all()
        for photo in useless_photos:
            db.session.delete(photo)
            db.session.query(PhotoAlternative).filter(PhotoAlternative.subject_id == photo.subject_id). \
                filter(PhotoAlternative.url == photo.url).delete()

    if photos - photos_exist != set():
        diff = True
        update_photos = Photo.query.filter(Photo.id.in_(photos - photos_exist)).all()
        for photo in update_photos:
            photo.subject_id = subject.id
            db.session.add(photo)

    if diff:
        db.session.commit()
    return diff

@subject_blueprint.route('/subject/bind_camera',methods=['PUT', 'DELETE'])
@login_required
@permission_required_page(AccountPermission.ADMIN)
def subject_bind_camera_operation():
    params = request.form or request.get_json() or request.args
    try:
        subject_id = int(params.get('subject_id'))
        screen_id = int(params.get('screen_id'))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    company = g.user.company
    subject = Subject.query.filter_by(id=subject_id, company_id=company.id).first()
    if subject is None:
        return error_result(ErrorCode.ERROR_SUBJECT_NOT_EXIST)
    screen = Screen.query.filter(Screen.company_id == company.id, Screen.id == screen_id).first()
    if not screen:
        return error_result(ErrorCode.ERROR_SCREEN_NOT_EXIST)
    if request.method == 'PUT':
        allowed_subject_ids = set(screen.allowed_subject_ids) | set([subject_id])
    elif request.method == 'DELETE':
        allowed_subject_ids = set(screen.allowed_subject_ids) - set([subject_id])
    else:
        return error_result(ErrorCode.ERROR_METHOD_ERROR)
    screen.allowed_subject_ids = list(allowed_subject_ids)

    try:
        db.session.add(screen)
        update_company_data_version(company)
    except:
        traceback.print_exc()
        db.session.rollback()
        return error_result(ErrorCode.ERROR_UNKNOWN)
    return success_result()


@subject_blueprint.route('/subject/bind_camera/copy', methods=['PUT'])
@login_required
@permission_required_page(AccountPermission.ADMIN)
def subject_bind_camera_operation_copy():
    params = request.form or request.get_json() or request.args
    try:
        from_screen_id = int(params.get('from_screen_id'))
        to_screen_id = int(params.get('to_screen_id'))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    company = g.user.company
    from_screen = Screen.query.filter_by(id=from_screen_id, company_id=company.id).first()
    if not from_screen:
        return error_result(ErrorCode.ERROR_SCREEN_NOT_EXIST, {"msg": "from screen not exits"})
    to_screen = Screen.query.filter_by(id=to_screen_id, company_id=company.id).first()
    if not to_screen:
        return error_result(ErrorCode.ERROR_SCREEN_NOT_EXIST, {"msg": "to screen not exits"})

    to_screen.allowed_subject_ids = from_screen.allowed_subject_ids
    to_screen.allow_all_subjects = from_screen.allow_all_subjects
    to_screen.allow_visitor = from_screen.allow_visitor

    try:
        db.session.add(to_screen)
        update_company_data_version(company)
    except:
        traceback.print_exc()
        db.session.rollback()
        return error_result(ErrorCode.ERROR_UNKNOWN)
    return success_result()


@subject_blueprint.route('/subject', methods=['POST'])
@login_required
def subject_new():
    params = request.form or request.get_json()
    try:
        company_id = g.user.company_id
        subject_type = int(params['subject_type'])
        visitor_type = SubjectType.TYPE_VISITOR if params.get('visitor_type') is None else int(params['visitor_type'])
        name = params.get('name', '').strip()
        email = params.get('email', '').strip()
        phone = params.get('phone', '').strip()
        gender = int(params.get('gender', Gender.MALE))
        avatar = params.get('avatar', '')
        department = params.get('department', '')
        title = params.get('title', '')
        description = params.get('description', '')
        start_time = 0 if subject_type in SubjectType.UN_CHECK_TIME else int(params['start_time'])
        end_time = 0 if subject_type in SubjectType.UN_CHECK_TIME else int(params['end_time'])
        photo_ids = params['photo_ids'] if 'photo_ids' in params else []
        if not isinstance(photo_ids, list):
            return error_result(ErrorCode.ERROR_INVALID_PARAM, {'msg': 'photo_ids需要list数据类型'})
        purpose = int(params.get('purpose', VisitorPurpose.OTHER))
        interviewee = params.get('interviewee', '')
        come_from = params.get('come_from', '')
        job_number = params.get('job_number', '')
        remark = params.get('remark', '')
        birthday = int(params.get('birthday', 0))
        entry_date = int(params.get('entry_date', 0))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    if subject_type not in SubjectType.state_mapping.keys():
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    if name == '' or start_time > end_time:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    if ((subject_type == SubjectType.TYPE_VISITOR
         and not g.user.has_permission(AccountPermission.ADD_VISITOR))
        or (subject_type == SubjectType.TYPE_EMPLOYEE
            and not g.user.has_permission(AccountPermission.ADD_EMPLOYEE))
        ):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)

    if ((subject_type == SubjectType.TYPE_YELLOWLIST)
            and not g.user.has_permission(AccountPermission.ADD_YELLOWLIST)):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)
    # VIP type
    if subject_type == SubjectType.TYPE_VISITOR:
        subject_type = visitor_type

    if email and Subject.query.filter_by(email=email).first():
        return error_result(ErrorCode.ERROR_EMAIL_EXISTED)

    subject = Subject(company_id=company_id,
                      subject_type=subject_type,
                      name=name,
                      email=email,
                      department=department,
                      gender=gender,
                      avatar=avatar,
                      title=title,
                      description=description,
                      start_time=start_time,
                      end_time=end_time,
                      password='123456',
                      purpose=purpose,
                      interviewee=interviewee,
                      phone=phone,
                      come_from=come_from,
                      job_number=job_number,
                      remark=remark,
                      create_time=g.TIMESTAMP)
    if birthday:
        subject.birthday = datetime.date.fromtimestamp(birthday)
    if entry_date:
        subject.entry_date = datetime.date.fromtimestamp(entry_date)
    if interviewee:
        subject.add_interviewee_pinyin(interviewee)
    if department:
        subject.add_department_pinyin(department)
    if subject.avatar:
        avatar = storage.save_image_base64(subject.avatar, 'avatar', sync=True)
        if avatar:
            subject.avatar = avatar
        DisplayDevice.query.filter_by(company_id=company_id).update({'user_info_timestamp': g.TIMESTAMP})
    try:
        db.session.add(subject)
        db.session.commit()
        _update_photos(subject, photo_ids)
        if photo_ids:
            update_company_data_version(subject.company, subject.id)
        log_Operation.save_log(HANDLE_SUBJECT_ADD + u'姓名:'+ name + u'小区编号:' + job_number)
        return success_result(subject.get_json(with_photos=True))
    except:
        traceback.print_exc()
        db.session.rollback()
        return error_result(ErrorCode.ERROR_UNKNOWN)


@subject_blueprint.route('/subject/file', methods=['POST'])
@login_required
def subject_new_file():
    """新subject创建接口,支持直接传入photo绑定"""
    params = request.form or request.get_json()
    payload = request.files.get('photo', None)
    _err_payload = check_upload_image(payload)
    if _err_payload:
        return _err_payload
    photo = None
    avatar = request.files.get('avatar', None)
    _err_avatar = check_upload_image(avatar)
    if _err_avatar:
        return _err_avatar
    try:
        company_id = g.user.company_id
        subject_type = int(params['subject_type'])
        visitor_type = SubjectType.TYPE_VISITOR if params.get('visitor_type') is None else int(params['visitor_type'])

        # name = params.get('name', '').strip()
        # email = params.get('email', '').strip()
        # phone = params.get('phone', '').strip()
        # gender = int(params.get('gender', Gender.MALE))
        # department = params.get('department', '')
        # title = params.get('title', '')
        # description = params.get('description', '')

        gender = int(params.get('gender', Gender.MALE))
        name = get_str_param_check_length('name', 20, params)
        email = is_mail('email', params)
        phone = get_str_param_check_length('phone', 20, params)
        department = get_str_param_check_length('department', 20, params)
        title = get_str_param_check_length('title', 20, params)
        description = get_str_param_check_length('description', 64, params)

        start_time = 0 if subject_type in SubjectType.UN_CHECK_TIME else int(params['start_time'])
        end_time = 0 if subject_type in SubjectType.UN_CHECK_TIME else int(params['end_time'])
        purpose = int(params.get('purpose', VisitorPurpose.OTHER))
        birthday = int(params.get('birthday', 0))
        entry_date = int(params.get('entry_date', 0))


        # interviewee = params.get('interviewee', '')
        # come_from = params.get('come_from', '')
        # job_number = params.get('job_number', '')
        # remark = params.get('remark', '')

        interviewee = get_str_param_check_length('interviewee', 20, params)
        come_from = get_str_param_check_length('come_from', 20, params)
        job_number = get_str_param_check_length('job_number', 20, params)
        remark = get_str_param_check_length('remark', 20, params)
    except Exception as err:
        logger.error(traceback.format_exc())
        return error_result(ErrorCode.ERROR_INVALID_PARAM, {"msg": err.message})
    # except:
    #     logger.error(traceback.format_exc())
    #     return error_result(ErrorCode.ERROR_INVALID_PARAM)
    if subject_type not in SubjectType.state_mapping.keys():
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    if name == '' or start_time > end_time:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    if ((subject_type == SubjectType.TYPE_VISITOR
         and not g.user.has_permission(AccountPermission.ADD_VISITOR))
        or (subject_type == SubjectType.TYPE_EMPLOYEE
            and not g.user.has_permission(AccountPermission.ADD_EMPLOYEE))
        ):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)

    if ((subject_type == SubjectType.TYPE_YELLOWLIST) and not g.user.has_permission(AccountPermission.ADD_YELLOWLIST)):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)

    # VIP type
    if subject_type == SubjectType.TYPE_VISITOR:
        subject_type = visitor_type

    if email and Subject.query.filter_by(email=email).first():
        return error_result(ErrorCode.ERROR_EMAIL_EXISTED)

    subject = Subject(company_id=company_id,
                      subject_type=subject_type,
                      name=name,
                      email=email,
                      department=department,
                      gender=gender,
                      title=title,
                      description=description,
                      start_time=start_time,
                      end_time=end_time,
                      password='123456',
                      purpose=purpose,
                      interviewee=interviewee,
                      phone=phone,
                      come_from=come_from,
                      job_number=job_number,
                      remark=remark,
                      create_time=g.TIMESTAMP)
    if birthday:
        subject.birthday = datetime.date.fromtimestamp(birthday)
    if entry_date:
        subject.entry_date = datetime.date.fromtimestamp(entry_date)
    if interviewee:
        subject.add_interviewee_pinyin(interviewee)
    if department:
        subject.add_department_pinyin(department)
    if avatar:
        avatar_url = create_user_avatar(avatar)
        if avatar_url:
            subject.avatar = avatar_url
        DisplayDevice.query.filter_by(company_id=company_id).update({'user_info_timestamp': g.TIMESTAMP})
    try:
        db.session.add(subject)
        db.session.commit()
        if payload:
            photo, error = create_user_photo(payload, company_id)
            if error:
                return error
        if photo:
            photo.subject_id = subject.id
            db.session.add(photo)
            update_company_data_version(subject.company, subject.id)
        return success_result(subject.get_json(with_photos=True))
    except:
        traceback.print_exc()
        db.session.rollback()
        return error_result(ErrorCode.ERROR_UNKNOWN)


@subject_blueprint.route('/subject/<int:sid>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def subject_detail(sid):
    params = request.form or request.get_json() or request.args

    subject = g.user.company.subjects.filter_by(id=sid).first()
    if not subject:
        return error_result(ErrorCode.ERROR_SUBJECT_NOT_EXIST)

    if request.method == 'GET':
        log_Operation.save_log(HANDLE_SUBJECT_CHECK + u'id:' + sid)
        return success_result(subject.get_json(with_photos=True))

    subject_type = subject.subject_type
    if ((subject_type == SubjectType.TYPE_VISITOR and not g.user.has_permission(AccountPermission.ADD_VISITOR)) or
            (subject_type == SubjectType.TYPE_EMPLOYEE and not g.user.has_permission(AccountPermission.ADD_EMPLOYEE))):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)

    if request.method == 'PUT':
        log_Operation.save_log(HANDLE_SUBJECT_UPDATE + u'id:' + sid)
        email = params.get('email')
        avatar = params.get('avatar')
        if email and Subject.query.filter(Subject.id != sid, Subject.email == email).first():
            return error_result(ErrorCode.ERROR_EMAIL_EXISTED)

        if params.get('visitor_type') is not None:
            params['subject_type'] = params['visitor_type']
        fields = (
            'subject_type',
            'description',
            'title',
            'gender',
            'start_time',
            'end_time',
            'department',
            'name',
            'email',
            'phone',
            'purpose',
            'interviewee',
            'come_from',
            'job_number',
            'remark'
        )
        subject.update(fields, params)
        # fix jira KOAL-1657
        if subject.start_time > subject.end_time:
            return error_result(ErrorCode.ERROR_INVALID_PARAM, {"msg": u"开始时间大于结束时间"})
        if avatar is None:
            pass
        elif avatar.startswith('http'):
            pass
        elif avatar == '':
            storage.remove(subject.avatar)
            subject.avatar = ''
        elif avatar.startswith('data:image'):
            avatar_url = storage.save_image_base64(avatar, 'avatar', sync=True)
            if avatar_url:
                storage.remove(subject.avatar)
                subject.avatar = avatar_url
            DisplayDevice.query.filter_by(company_id=subject.company.id).update({'user_info_timestamp': g.TIMESTAMP})

        if 'birthday' in params:
            subject.birthday = datetime.date.fromtimestamp(int(params['birthday'])) if params['birthday'] else None
        if 'entry_date' in params:
            subject.entry_date = datetime.date.fromtimestamp(int(params['entry_date'])) if params[
                'entry_date'] else None
        
        photo_ids = params.get('photo_ids')
        if not isinstance(photo_ids, list):
            return error_result(ErrorCode.ERROR_INVALID_PARAM, {'msg': 'photo_ids需要list数据类型'})
        if subject.photos or photo_ids:
            _update_photos(subject, photo_ids)
            update_company_data_version(subject.company, subject.id)
        db.session.add(subject)
        db.session.commit()

    elif request.method == 'DELETE':
        log_Operation.save_log(HANDLE_SUBJECT_DELETE + u'id:' + sid)
        # for photo in subject.photos:                
        #     storage.remove(photo.url)
        db.session.delete(subject)
        db.session.commit()
        update_company_data_version(subject.company, subject.id)
    return success_result(subject.get_json(with_photos=True))


@subject_blueprint.route('/subject/reset-password/<int:sid>', methods=['PUT'])
@login_required
def subject_reset_password(sid):
    subject = Subject.query.get(sid)
    if subject is None:
        return error_result(ErrorCode.ERROR_SUBJECT_NOT_EXIST)
    subject.password = '123456'
    subject.password_reseted = False
    db.session.add(subject)
    db.session.commit()
    return success_result()


@subject_blueprint.route('/subject/avatar', methods=['POST'])
@login_required
def subject_avatar_create():
    try:
        avatar = request.files['avatar']
        subject_id = int(request.form.get('subject_id', 0))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    if not avatar:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    try:
        image_uri = storage.save_image(avatar.stream, 'avatar', resize=(300, 300), sync=True)
    except IOError:
        return error_result(ErrorCode.ERROR_BAD_IMAGE)
    if not image_uri:
        return error_result(ErrorCode.ERROR_UNKNOWN)

    if subject_id:
        subject = Subject.query.get(subject_id)
        subject.avatar = image_uri
        db.session.add(subject)
        db.session.commit()
        update_company_data_version(g.user.company, subject.id)
    return success_result({'url': storage.get_url(image_uri)})


@subject_blueprint.route('/subject/photo', methods=['POST'])
@login_required
def subject_photo_create():
    log_Operation.save_log(HANDLE_SUBJECT_UPLOAD_PHOTO)
    try:
        payload = request.files['photo']
        subject_id = int(request.form.get('subject_id', 0))
        old_photo_id = int(request.form.get('old_photo_id', 0))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    if not payload:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    if not g.user.company_id:
        return error_result(ErrorCode.ERROR_USER_NOT_BIND_COMPANY)

    if subject_id:
        subject = Subject.query.filter_by(id=subject_id).first()
        if subject is None:
            return error_result(ErrorCode.ERROR_INVALID_PARAM)
        subject_type = subject.subject_type
        if ((subject_type == SubjectType.TYPE_VISITOR and not g.user.has_permission(AccountPermission.ADD_VISITOR)) or
                (subject_type == SubjectType.TYPE_EMPLOYEE and not g.user.has_permission(
                    AccountPermission.ADD_EMPLOYEE))):
            return error_result(ErrorCode.ERROR_PERMISSION_DENIED)

    photo, error = create_user_photo(payload, g.user.company_id)
    if error:
        return error

    # delete old photo
    if subject_id and old_photo_id:
        old_photo = Photo.query.get(old_photo_id)
        if old_photo and old_photo.subject_id == subject_id:
            storage.remove(old_photo.url)
            db.session.delete(old_photo)
            db.session.query(PhotoAlternative).filter(PhotoAlternative.subject_id == old_photo.subject_id). \
                filter(PhotoAlternative.url == old_photo.url).delete()

    if subject_id:
        photo.subject_id = subject_id
    db.session.add(photo)
    db.session.commit()
    if subject_id:
        update_company_data_version(g.user.company, subject.id)
    return success_result(photo.get_json(with_origin=True))


@subject_blueprint.route('/subject/photo', methods=['DELETE'])
@login_required
def subject_photo_delete_all():
    try:
        subject_id = int(request.form.get('subject_id', 0))
        log_Operation.save_log(HANDLE_SUBJECT_DELETE_PHOTO + u'人员id:' + str(subject_id))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    # 公司要有
    cid = g.user.company_id
    if not cid:
        return error_result(ErrorCode.ERROR_USER_NOT_BIND_COMPANY)
    # 公司要有这个subject
    subject = Subject.query.filter_by(id=subject_id).filter_by(company_id=cid).first()
    if subject is None:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    # 要有权限
    if not g.user.has_permission(AccountPermission.ADD_VISITOR) \
            or not g.user.has_permission(AccountPermission.ADD_EMPLOYEE):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)

    try:
        Photo.query.filter(Photo.subject_id == subject_id).filter(Photo.company_id == cid).delete()
        PhotoAlternative.query.filter(PhotoAlternative.subject_id == subject_id)\
            .filter(PhotoAlternative.company_id == cid).delete()
        db.session.commit()
        update_company_data_version(g.user.company, subject_id)
    except:
        logger.error(traceback.format_exc())
        return error_result(ErrorCode.ERROR_UNKNOWN)

    return success_result({})


@subject_blueprint.route('/subject/photo/check', methods=['POST'])
@login_required
def subject_photo_check():
    try:
        payload = request.files['photo']
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    if not payload:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    _err_payload = check_upload_image(payload)
    if _err_payload:
        return _err_payload

    photo, error = create_user_photo(payload, g.user.company_id, check_only=True)
    if error:
        return error
    return success_result({})


@subject_blueprint.route('/subject/department/list')
@login_required
def subject_department_list():
    log_Operation.save_log(HANDLE_SUBJECT_VIEW)
    cid = g.user.company_id
    if not cid:
        return error_result(ErrorCode.ERROR_USER_NOT_BIND_COMPANY)
    res = db.session.query(Subject.department).filter(Subject.company_id == cid).distinct(Subject.department).all()
    data = []
    for d in res:
        if d[0]:
            data.append(d[0])
    return success_result({"department": data})


@subject_blueprint.route('/subject/list/delete', methods=['DELETE'])
@login_required
def subject_list_delete():
    params = request.get_json() or request.form or request.args
    try:
        params = params.to_dict()
    except:
        logger.info(traceback.format_exc())
        return error_result(ErrorCode.ERROR_UNKNOWN)
    cid = g.user.company_id
    if not cid:
        return error_result(ErrorCode.ERROR_USER_NOT_BIND_COMPANY)
    if not g.user.has_permission(AccountPermission.ADD_VISITOR) \
            or not g.user.has_permission(AccountPermission.ADD_EMPLOYEE):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)

    params['company_id'] = cid
    task = Task()
    task.name = u"批量删除subject"
    task.user_id = g.user.id
    task.company_id = g.user.company_id
    task.content = "exec by %s" % g.user.username
    db.session.add(task)
    db.session.commit()
    delete_subjects.queue(task.id, params)
    return success_result({})



def _import_photo_cache(uuid, filename, code, desc):
    _cache_data = redis.db.get('import_photo:%s' % uuid)
    if _cache_data:
        cache_data = json.loads(_cache_data)
    else:
        cache_data = {'res': []}
    cache_data['res'].append((filename, code, desc))
    redis.db.setex('import_photo:%s' % uuid, json.dumps(cache_data), 86400)


def _import_photo_cache_incr(uuid):
    key = "import_photo_num:%s" % uuid
    redis.db.incr(key)


def _import_photo_cache_decr(uuid):
    key = "import_photo_num:%s" % uuid
    redis.db.decr(key)


def _import_photo_cache_num(uuid):
    key = "import_photo_num:%s" % uuid
    return redis.db.get(key)


def _import_photo_cache_delete(uuid):
    key = "import_photo_num:%s" % uuid
    return redis.db.delete(key)


@subject_blueprint.route('/subject/import-photo', methods=['POST'])
@login_required
def subject_import_photo():
    try:
        photo = request.files['photo']
        name = photo.filename.rsplit('.', 1)[0]
        _filename = photo.filename
        # 判断导入的是员工还是访客
        if request.args.get('subject_type'):
            subject_type = int(request.args.get('subject_type'))
        else:
            subject_type = SubjectType.TYPE_EMPLOYEE
        # 支持的匹配模式 job_number, phone, real_name
        match_opt = request.args.get('match_opt')  # 传入参数j, p, n
        import_uuid = request.args.get('iuuid')
    except:
        if import_uuid:
            _import_photo_cache(import_uuid, _filename, -1002, u'参数错误')
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    if import_uuid:
        _import_photo_cache_incr(import_uuid)

    if len(name) > 20:
        if import_uuid:
            _import_photo_cache(import_uuid, _filename, -1002, "file name len bigger than 20")
            _import_photo_cache_decr(import_uuid)
        return error_result(ErrorCode.ERROR_INVALID_PARAM, {"msg": "file name len bigger than 20"})
    photo, error = create_user_photo(photo, g.user.company_id)
    if error:
        if import_uuid:
            _e_obj = json.loads(error.data)
            _import_photo_cache(import_uuid, _filename, _e_obj['code'], _e_obj['desc'])
            _import_photo_cache_decr(import_uuid)
        return error

    flag = True
    new_status = ImportPhotoType.NEW
    if match_opt:
        match_opt = str(match_opt)
        _q = Subject.query.filter(Subject.company_id == g.user.company_id)
        _q = _q.filter(Subject.subject_type == int(subject_type))
        if match_opt == 'j':
            _q = _q.filter(Subject.job_number == str(name))
        elif match_opt == 'p':
            _q = _q.filter(Subject.phone == str(name))
        elif match_opt == 'n':
            _q = _q.filter(Subject.real_name == str(name))
        else:
            if import_uuid:
                _import_photo_cache(import_uuid, _filename, -1002, u'参数错误')
                _import_photo_cache_decr(import_uuid)
            return error_result(ErrorCode.ERROR_INVALID_PARAM)

        count = _q.count()
        # _cache_data = redis.db.get('import_photo:%s' % import_uuid)
        # if _cache_data:
        #     cache_data = json.loads(_cache_data)
        #     for _tuple in cache_data['res']:
        #         _f = _tuple[0].rsplit('.', 1)[0]
        #         if _f == name:
        #             count = count + 1
        # logger.debug("uuid:%s name:%s count:%s" % (import_uuid, name, count))
        if count == 1:
            flag = False
            subject = _q.first()
            _import_photo_cache(import_uuid, _filename, 0, ImportPhotoType.MATCH)
            new_status = ImportPhotoType.MATCH
        elif count == 0:
            if import_uuid:
                _import_photo_cache(import_uuid, _filename, 0, ImportPhotoType.NOMATCH)
                new_status = ImportPhotoType.NOMATCH
        elif count > 0:
            if import_uuid:
                _import_photo_cache(import_uuid, _filename, 0, ImportPhotoType.MORETHANONE)
                new_status = ImportPhotoType.MORETHANONE
    else:
        _import_photo_cache(import_uuid, _filename, 0, ImportPhotoType.NEW)
    if flag:
        subject = Subject(company_id=g.user.company_id, subject_type=subject_type,
                          name=name, create_time=g.TIMESTAMP)
        if match_opt:
            if match_opt == 'j':
                subject.job_number = name
            elif match_opt == 'p':
                subject.phone = name
            elif match_opt == 'n':
                subject.real_name = name
        db.session.add(subject)
    # 替换最新图片
    if len(subject.photos) >= 3:
        max_p, max_p_id = None, 0
        for p in subject.photos:
            if p.id > max_p_id:
                max_p = p
        db.session.delete(max_p)
    subject.photos.append(photo)
    db.session.commit()
    update_company_data_version(g.user.company, subject.id)
    return_data = subject.get_json(with_photos=True, origin_photos=True)
    return_data['new_status'] = new_status
    _import_photo_cache_decr(import_uuid)
    return success_result(return_data)


@subject_blueprint.route('/subject/import-photo/type')
@login_required
def get_imphototype():
    return success_result(ImportPhotoType.state_mapping)


@subject_blueprint.route('/subject/import-photo/xlsx')
@login_required
def subject_import_photo_xlsx():
    try:
        import_uuid = str(request.args.get('iuuid'))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    from config import BASEDIR
    path = "%s/app/static/upload/import_photo_xlsx" % BASEDIR
    if not os.path.exists(path):
        os.makedirs(path)
    filename = "%s/%s.xlsx" % (path, import_uuid)
    en = u'导入图片结果.xlsx'
    if os.path.isfile(filename):
        os.remove(filename)
        # return send_file(filename, mimetype='application/octet-stream', as_attachment=True, attachment_filename=en,
        #                  cache_timeout=0)

    _cache_data = redis.db.get('import_photo:%s' % import_uuid)
    if _cache_data:
        cache_data = json.loads(_cache_data)
    else:
        return error_result(ErrorCode.ERROR_INVALID_PARAM, {'msg': "no data for this import uuid"})

    workhook = xlsxwriter.Workbook(filename)
    worksheet = workhook.add_worksheet(u'结果')
    worksheet.write(0, 0, u'id')
    worksheet.write(0, 1, u'照片文件名')
    worksheet.write(0, 2, u'code')
    worksheet.write(0, 3, u'导入结果')
    i = 1
    for _tuple in cache_data['res']:
        worksheet.write(i, 0, i)
        worksheet.write(i, 1, _tuple[0])
        worksheet.write(i, 2, _tuple[1])
        if _tuple[1] == 0:
            worksheet.write(i, 3, ImportPhotoType.get_desc(_tuple[2]))
        else:
            worksheet.write(i, 3, _tuple[2])
        i = i + 1

    workhook.close()
    # 不主动删除数据
    # redis.db.delete('import_photo:%s' % import_uuid)
    return send_file(filename, mimetype='application/octet-stream', as_attachment=True, attachment_filename=en,
                     cache_timeout=0)


@subject_blueprint.route('/subject/import-photo/result')
@login_required
def subject_import_photo_result():
    try:
        import_uuid = str(request.args.get('iuuid'))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    num = _import_photo_cache_num(import_uuid)
    if num:
        num = int(num)
    for i in range(0, 10):
        if num and num > 0:
            time.sleep(1)
            logger.debug("num %s --- %s" % (num, time.time()))
        else:
            break
    _import_photo_cache_delete(import_uuid)

    _cache_data = redis.db.get('import_photo:%s' % import_uuid)
    if _cache_data:
        cache_data = json.loads(_cache_data)
    else:
        return error_result(ErrorCode.ERROR_INVALID_PARAM, {'msg': "no data for this import uuid"})

    result = {"0": 0, "1": 0, "2": 0, "3": 0, "-1": 0}
    for _tuple in cache_data['res']:
        key = "-1"
        if _tuple[1] == 0:
            key = str(_tuple[2])
        result[key] = result[key] + 1

    return success_result(result)


def _subject_import_employee(file_, subject_type):
    def validate(sheet):
        invalid, emails = {}, []
        # !!避免在for循环里做查询
        for i in xrange(1, sheet.nrows):
            row = sheet.row(i)
            if row[5].value:
                emails.append(row[5].value)
        duplicate = Subject.query.filter(Subject.email.in_(emails)).all()
        dup_emails = [item.email for item in duplicate]
        phone = re.compile('^[+]?[0-9 \-]{6,19}(.0)?$')
        email_validate = re.compile("[^@]+@[^@]+\.[^@]+")

        for i in xrange(1, sheet.nrows):
            row, errors = sheet.row(i), []
            cols = [str(item.value) for item in row]
            if ''.join(cols).strip():
                if not cols[0].strip():
                    errors.append(u'姓名为空')
                if cols[4] and not phone.match(cols[4]):
                    errors.append(u'手机号不合法')
                if cols[5] and not email_validate.match(cols[5]):
                    errors.append("邮箱不合法")
                if cols[5] in dup_emails:
                    errors.append(u'邮箱重复')
                # 上传文件内判重
                elif cols[5]:
                    dup_emails.append(cols[5])
            else:
                errors.append(u'空行')
            if errors:
                invalid[i] = errors
        return invalid

    try:
        workbook = xlrd.open_workbook(file_contents=file_.read())
    except:
        return error_result(ErrorCode.ERROR_EXCEL_INVALID)

    sheet = workbook.sheet_by_index(0)
    success = 0
    failed = validate(sheet)

    for i in xrange(1, sheet.nrows):
        try:
            row = sheet.row(i)
            if i in failed:
                continue
            db.session.add(Subject(name=row[0].value,
                                   job_number=row[1].value,
                                   department=row[2].value,
                                   password='123456',
                                   title=row[3].value,
                                   gender=0,
                                   phone=row[4].value,
                                   email=row[5].value,
                                   description=row[6].value,
                                   remark=row[7].value,
                                   subject_type=subject_type,
                                   company_id=g.user.company_id))
            db.session.commit()
            success += 1

        except:
            failed[i] = [u'保存出错']
            logger.error(traceback.format_exc())
    return success_result({'success': success, 'total': sheet.nrows - 1, 'failed': failed})


@subject_blueprint.route('/subject/import', methods=['POST'])
@login_required
def subject_import():
    try:
        file_ = request.files['file']
        # 判断导入的是员工还是访客
        if request.args['subject_type']:
            subject_type = request.args['subject_type']
            subject_type = int(subject_type)
        else:
            subject_type = SubjectType.TYPE_EMPLOYEE
    except:
        logger.error(traceback.format_exc())
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    if subject_type == SubjectType.TYPE_EMPLOYEE:
        if not g.user.has_permission(AccountPermission.ADD_EMPLOYEE):
            return error_result(ErrorCode.ERROR_PERMISSION_DENIED)
        return _subject_import_employee(file_, subject_type)
    else:
        if not g.user.has_permission(AccountPermission.ADD_VISITOR):
            return error_result(ErrorCode.ERROR_PERMISSION_DENIED)
        return _subject_import_visitor(file_, subject_type)


def _subject_import_visitor(file_, subject_type):
    workbook = xlrd.open_workbook(file_contents=file_.read())
    sheet = workbook.sheet_by_index(0)
    success = 0

    state_mapping = VisitorPurpose.state_mapping_reverse
    _mapping = VisitorInviteDays.state_mapping_reverse

    nt = datetime.datetime.now()
    _start = time.mktime(nt.timetuple())
    _end_t = datetime.datetime(nt.year, nt.month, nt.day, 23, 59, 59)
    end_time = time.mktime(_end_t.timetuple())

    failed = {}
    for i in xrange(1, sheet.nrows):
        errors = []
        try:
            row = sheet.row(i)
            name = row[0].value
            if not name:
                errors.append(u'姓名为空')
                continue
            purpose = VisitorPurpose.OTHER if not state_mapping.has_key(row[1].value) else state_mapping[row[1].value]
            invite_days = VisitorInviteDays.DAY if not _mapping.has_key(row[6].value) else _mapping[row[6].value]

            if invite_days > 0:
                end_t = nt + timedelta(invite_days)
                end_time = time.mktime(end_t.timetuple())

            db.session.add(
                Subject(name=name, purpose=purpose, come_from=row[2].value, phone=row[3].value,
                        interviewee=row[4].value, remark=row[5].value,
                        subject_type=subject_type, company_id=g.user.company_id,
                        start_time=_start, end_time=end_time))
            db.session.commit()
            success += 1
        except:
            errors.append(u'保存出错')
            logger.error(traceback.format_exc())
        finally:
            if errors:
                failed[i] = errors

    return success_result({'success': success, 'total': sheet.nrows - 1, 'failed': failed})


@subject_blueprint.route('/subject/import-visitor', methods=['POST'])
@login_required
def subject_import_visitor():
    try:
        file_ = request.files['file']
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    if not g.user.has_permission(AccountPermission.ADD_VISITOR):
        return error_result(ErrorCode.ERROR_PERMISSION_DENIED)
    return _subject_import_visitor(file_, SubjectType.TYPE_VISITOR)


@subject_blueprint.route('/subject/export.zip', methods=['GET'])
@login_required
def subject_export():
    params = request.form or request.get_json() or request.args
    def iterable(url, cdn):
        try:
            with open(cdn.get_path(url)) as f:
                yield f.read()
        except:
            yield ''

    def generator(subjects):
        data = []
        z = zipstream.ZipFile(mode='w', compression=zipstream.ZIP_DEFLATED)
        for subject in subjects:
            subject_json = subject.get_json()
            subject_json['photos'] = []
            for photo in subject.photos:
                if photo.origin_url:
                    url = photo.origin_url
                    cdn = oss
                else:
                    url = photo.url
                    cdn = storage
                short_path = '/'.join(url.split('/')[-2:])
                z.write_iter(short_path, iterable(url, cdn))
                subject_json['photos'].append(short_path)
            # fix export with out avatar
            avatar = subject.avatar
            if avatar:
                avatar_path = '/'.join(avatar.split('/')[-2:])
                z.write_iter(avatar_path, iterable(avatar, storage))
                subject_json['avatar'] = avatar_path
            data.append(subject_json)

        z.writestr('index.json', json.dumps(data, indent=2))
        for zip_data in z:
            yield zip_data

    # company = g.user.company
    # params = request.args
    # subjects,page = get_subject_list(company, params)

    """
    http://koala.pd.megvii-inc.com/subject/export.zip?subject_type=0&category=employee&name=dasfadf&department=&interviewee=
    固定 按名字搜索
    """
    subject_type = int(params.get("subject_type", 0))
    subject_type_list = []
    if subject_type == SubjectType.TYPE_EMPLOYEE:
        subject_type_list = [SubjectType.TYPE_EMPLOYEE]
    elif subject_type == SubjectType.TYPE_VISITOR:
        subject_type_list = [SubjectType.TYPE_VISITOR, SubjectType.TYPE_VIP]
    elif subject_type == SubjectType.TYPE_VIP:
        subject_type_list = [SubjectType.TYPE_VIP]
    elif subject_type == SubjectType.TYPE_YELLOWLIST:
        subject_type_list = [SubjectType.TYPE_YELLOWLIST]
    query =  g.user.company.subjects.filter(Subject.subject_type.in_(subject_type_list)) \
        .options(db.eagerload_all(Subject.photos))

    name = params.get('name')
    if name: 
        name = name.replace('\\', '\\\\')
        query = query.filter(Subject.real_name.contains(name) | Subject.pinyin.contains(name))

    # if subjects is None:subjects=[]
    response = Response(generator(query), mimetype='application/zip')
    response.headers['Content-Disposition'] = 'attachment; filename={}'.format('export.zip')
    return response


@subject_blueprint.route('/subject/import.zip', methods=['POST'])
@login_required
def subject_import_zip():
    try:
        upload = request.files['file']
        subject_type = int(request.args.get('subject_type'))
    except:
        return error_result(ErrorCode.ERROR_INVALID_PARAM)
    try:
        z = zipfile.ZipFile(upload, 'r')
        z.getinfo('index.json')
    except:
        return error_result(ErrorCode.ERROR_FILE_FORMAT_ERROR)
    if subject_type not in SubjectType.state_mapping.keys():
        return error_result(ErrorCode.ERROR_INVALID_PARAM)

    def generator(company):
        z = zipfile.ZipFile(upload, 'r')
        data = json.loads(z.read('index.json'))
        total = len(data)
        now = 0
        email_dup = 0
        try:
            for subject_data in data:
                now += 1
                error_code = None
                try:
                    photos = []
                    success_photo = failed_photo = 0

                    for photo in subject_data['photos']:
                        try:
                            _, ext = os.path.splitext(photo)
                            photo, error = create_user_photo(
                                StringIO(z.read(photo)), company.id, extension=ext)
                        except:
                            logger.error(traceback.format_exc())
                            error = error_result(ErrorCode.ERROR_UNKNOWN)
                        if not error:
                            photos.append(photo)
                            success_photo += 1
                        else:
                            failed_photo += 1

                    del subject_data['id']
                    del subject_data['photos']
                    subject_data['company_id'] = company.id

                    avatar_path = subject_data.get('avatar', '')
                    if avatar_path:
                        try:
                            _, ext = os.path.splitext(avatar_path)
                            subject_data['avatar'] = storage.save_image(
                                StringIO(z.read(avatar_path)),
                                category='avatar', extension=ext
                            )
                        except:
                            logger.error("save avatar fail %s" % avatar_path)
                            subject_data['avatar'] = ''
                    else:
                        subject_data['avatar'] = ''
                    # 检查数据长度
                    _check_key_len = [('name', 20), ('job_number', 15), ('department', 20),
                                      ('description', 30), ('title', 20), ('email', 30),
                                      ('phone', 20), ('remark', 30)]
                    for _tuple in _check_key_len:
                        if len(subject_data[_tuple[0]]) > _tuple[1]:
                            error_code = ErrorCode.ERROR_INVALID_PARAM

                    date = subject_data['birthday']
                    if type(date) is int:
                        subject_data['birthday'] = datetime.date.fromtimestamp(date)

                    date = subject_data['entry_date']
                    if type(date) is int:
                        subject_data['entry_date'] = datetime.date.fromtimestamp(date)

                    # 判断email是否已经存在
                    email = subject_data['email']
                    if not re.match("[^@]+@[^@]+\.[^@]+", email):
                        # 不合法的email清空
                        subject_data['email'] = ''
                    elif Subject.query.filter_by(email=email).first():
                        email_dup += 1
                        error_code = ErrorCode.ERROR_EMAIL_EXISTED

                    # 判断导入的是员工还是访客
                    # fix jira KOAL-1772
                    _req_subject_type = int(request.args.get('subject_type'))
                    _import_subject_type = None
                    if "subject_type" in subject_data:
                        try:
                            _import_subject_type = int(subject_data["subject_type"])
                            if _import_subject_type not in SubjectType.state_mapping.keys():
                                _import_subject_type = None
                        except:
                            logger.error(traceback.format_exc())
                            _import_subject_type = None
                    if _req_subject_type == SubjectType.TYPE_VISITOR and _import_subject_type and _import_subject_type == SubjectType.TYPE_VIP:
                        subject_data["subject_type"] = _import_subject_type
                    else:
                        subject_data['subject_type'] = _req_subject_type

                    if error_code is None:
                        """
                        测试会从访客dump数据，导入员工,会引发js错误，需要删除下面key值
                        arg传入是字符串
                        """
                        if request.args.get('subject_type',None,int) == SubjectType.TYPE_EMPLOYEE:
                            for k in ('start_time','end_time'):
                                if k in subject_data: del subject_data[k]

                        subject = Subject(**subject_data)
                        subject.photos = photos
                        db.session.add(subject)
                        db.session.commit()
                        update_company_data_version(company, subject.id)
                except:
                    logger.error('/subject/import.zip exception: ' + str(traceback.format_exc()))
                    error_code = ErrorCode.ERROR_UNKNOWN

                    # 建议email字段默认为空字符串，如果有重复，则赋值
                yield json.dumps(
                    dict(now=now, total=total, name=subject_data.get('name'), success=success_photo, fail=failed_photo,
                         email=email if error_code == ErrorCode.ERROR_EMAIL_EXISTED else '')) + '\n'
            print 'email duplicate', email_dup
            z.close()
        except:
            db.session.rollback()
            z.close()

    response = Response(stream_with_context(generator(g.user.company)))
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@subject_blueprint.route('/subject/invite', methods=['GET', 'POST'])
def subject_invite_page():
    subject_id = request.args.get('subject_id')
    token = request.args.get('token')
    if not subject_id or not token:
        return "非法请求"
    try:
        subject_id = int(subject_id)
    except:
        return "非法请求"

    subject = Subject.query.filter(Subject.id == subject_id,
                                   Subject.subject_type != SubjectType.TYPE_EMPLOYEE).first()
    if not subject:
        return "链接已失效"

    if g.TIMESTAMP > subject.end_time or subject.job_number != token:
        return "链接已失效"

    if request.method == 'GET':
        return render_template('page/subject/invite.html', subject=subject.get_json(with_photos=True), error='')
    elif request.method == 'POST':
        payload = request.files['photo']
        photo, err = create_user_photo(payload, subject.company_id)
        if err:
            return err
        db.session.add(photo)
        db.session.commit()
        _update_photos(subject, [photo.id])
        update_company_data_version(subject.company, subject.id)
        return success_result(photo.get_json())


####zhucheng
import xlsxwriter
import tempfile
from app.models import AttendanceCalendar
from app.common.view_helper import _build_query


@subject_blueprint.route('/subject/export/<path:export_name>')
@login_required
def exports_zhucheng(export_name):
    def format_timestamp_zhucheng(timestamp):
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))

    def format_date_zhucheng(timestamp):
        return time.strftime('%Y-%m-%d', time.localtime(timestamp))

    def get_date_timestamo(date, hour_mimute_second):
        return time.mktime(time.strptime(date + ' ' + hour_mimute_second, '%Y-%m-%d %H:%M:%S'))

    def get_work_hour(work_status, date, first, last):
        if first and last:
            difference = 0
            start = get_date_timestamo(date, first)
            end = get_date_timestamo(date, last)
            hour_12 = get_date_timestamo(date, '12:00:00')
            hour_13 = get_date_timestamo(date, '13:00:00')
            if start < hour_12:
                if end < hour_12:
                    difference = end - start
                elif end >= hour_12 and end < hour_13:
                    difference = hour_12 - start
                elif end > hour_13:
                    if work_status == '工作日':
                        difference = end - start - 3600
                    elif work_status == '休息日':
                        difference = end - start
            elif start >= hour_12 and start < hour_13:
                if end < hour_13:
                    difference = 0
                elif end >= hour_13:
                    difference = end - hour_13
            elif start >= hour_13:
                difference = end - start
            return round(difference / 3600 + 0.01, 1)
        return 0

    ##获取午休两次时间
    def cal_rest_first_time(rest):
        if rest:
            return rest[0]
        return ''

    def cal_rest_last_time(rest):
        if rest:
            if len(rest) == 2:
                return rest[1]
        return ''

    ##判断是否提前下班
    def is_zaotui_loudaka(date, time):
        if time:
            hour_18 = get_date_timestamo(date, '18:00:00')
            now = get_date_timestamo(date, time)
            if now > hour_18:
                return '否'
        return '是'

    ##判断是否有加班餐
    def is_have_jiabancan(work_status, date, time, work_hour):
        if work_status == '工作日':
            if time:
                hour_21 = get_date_timestamo(date, '21:00:00')
                now = get_date_timestamo(date, time)
                if now > hour_21 and work_hour > 8:
                    return '有'
        elif work_status == '休息日':
            if work_hour >= 4:
                return '有'
        return None

    ##加班时长
    def jiaban_hour(work_status, work_hour):
        if work_status == '工作日':
            return work_hour - 8 if work_hour > 8 else None
        elif work_status == '休息日':
            return work_hour
        return None

    ##处理昨日或者今日最后一条记录 同时计算午休时间段
    def dealLastDayTime(last, last_day_time, rest, riqi):
        size = len(last_day_time)
        if size > 0:
            # 打印最后一次记录
            last.append(format_timestamp_zhucheng(last_day_time[size - 1]).split(' ')[1])
            ##计算午休时间段
            date_12_timestamp = get_date_timestamo(riqi[len(riqi) - 1], '12:00:00')
            date_13_timestamp = get_date_timestamo(riqi[len(riqi) - 1], '13:00:00')
            rest_time = []
            for day_time in last_day_time:
                if day_time > date_12_timestamp and day_time < date_13_timestamp:
                    rest_time.append(format_timestamp_zhucheng(day_time).split(' ')[1])
            if len(rest_time) > 2:
                first_rest_time = rest_time[0]
                last_rest_time = rest_time[len(rest_time) - 1]
                rest_time = [first_rest_time, last_rest_time]
            rest.insert(len(riqi) - 1, rest_time)

    # 时间
    start = request.args.get('start')
    end = request.args.get('end')

    ##获取考勤日历信息 因为可能牵扯到跨年查询 故查询出2016至今的所有考勤日历
    calendar = {}
    for year in range(2016, datetime.date.today().year + 1):
        calendar[year] = AttendanceCalendar.get_or_create(g.user.company_id, year,
                                                          json.loads(g.user.company.attendance_weekdays), True)

    def work_rest_status(date):
        cal_date = datetime.datetime.strptime(date, '%Y-%m-%d')
        if calendar[cal_date.year].check_day(cal_date):
            return '工作日'
        return '休息日'

    subjects = g.user.company.subjects.filter(or_(Subject.subject_type == SubjectType.TYPE_EMPLOYEE,
                                                  Subject.end_time > time.time())).options(
        db.eagerload_all(Subject.photos))

    temp = tempfile.TemporaryFile()
    workbook = xlsxwriter.Workbook(temp)
    worksheet = workbook.add_worksheet("subject")

    worksheet.write(0, 0, u'部门')
    worksheet.write(0, 1, u'姓名')
    worksheet.write(0, 2, u'日期')
    worksheet.write(0, 3, u'首次打卡时间')
    worksheet.write(0, 4, u'末次打卡时间')
    worksheet.write(0, 5, u'午休首次打卡')
    worksheet.write(0, 6, u'午休末次打卡')
    worksheet.write(0, 7, u'工作时长(小时)')
    worksheet.write(0, 8, u'状态')
    worksheet.write(0, 9, u'加班时长')
    worksheet.write(0, 10, u'是否提前下班')
    worksheet.write(0, 11, u'是否有特殊加班')
    worksheet.write(0, 12, u'出勤天数"工作日"')
    worksheet.write(0, 13, u'日常加班小时')
    worksheet.write(0, 14, u'节假日加班小时')
    worksheet.write(0, 15, u'是否有加班餐')
    worksheet.write(0, 16, u'加班餐数量')

    row_index = 1
    for i, subject in enumerate(subjects):
        ##打印名字
        event_day = []
        last_day_time = []
        name = subject.name
        department = subject.department
        riqi = []
        first = []
        last = []
        rest = [[]]
        params = {'start': start, 'end': end, 'subject_id': subject.id}
        ##倒序
        _, query = _build_query(params, g.user.company_id)
        events = query.all()
        for j, event in enumerate(events):
            date = format_date_zhucheng(event.timestamp)
            if event_day.__contains__(date) == False:
                event_day.append(date)
                # day = format_date_zhucheng(date)
                day_first_time = event.timestamp
                dealLastDayTime(last, last_day_time, rest, riqi)
                last_day_time = []
                # 打印日期
                riqi.append(date)
                # 打印今日第一次记录
                first.append(format_timestamp_zhucheng(day_first_time).split(' ')[1])
            last_day_time.append(event.timestamp)
        ##处理最后一天数据
        dealLastDayTime(last, last_day_time, rest, riqi)

        ##打印需要写入excel的信息
        for k, date in enumerate(riqi):
            # ##部门
            worksheet.write(row_index, 0, department)
            # ##名字
            worksheet.write(row_index, 1, name)
            # ##当天日期
            worksheet.write(row_index, 2, date)
            # ##第一次打卡时间
            worksheet.write(row_index, 3, last[k])
            # ##最后一次打卡时间
            worksheet.write(row_index, 4, first[k])
            # ##午休时间第一次打卡
            worksheet.write(row_index, 5, cal_rest_last_time(rest[k]))
            # ##午休时间最后一次打卡
            worksheet.write(row_index, 6, cal_rest_first_time(rest[k]))
            # 获取工作日状态\工作时间(工作日减去午休一小时吃饭)
            work_status = work_rest_status(date)
            work_hour = get_work_hour(work_status, date, last[k], first[k])
            # ##工作时间计算
            worksheet.write(row_index, 7, work_hour)
            # ##根据考勤日历判断属于工作日还是休息天
            worksheet.write(row_index, 8, work_status)
            # 加班时长 等于工作时长减8（午休一小时已经减掉），如果大于零，就将值写入，小于0就不统计 休息日=实际工作时长
            worksheet.write(row_index, 9, jiaban_hour(work_status, work_hour))
            # 是否提前下班 有早退或者漏打卡的在此处标上。
            worksheet.write(row_index, 10, is_zaotui_loudaka(date, first[k]))
            # 是否有特殊加班 不统计
            worksheet.write(row_index, 11, None)
            # 出勤天数"工作日" 此处值填1
            worksheet.write(row_index, 12, 1 if work_status == '工作日' else None)
            # 日常加班小时 如果是工作日，此处等于加班时长
            worksheet.write(row_index, 13, work_hour - 8 if work_status == '工作日' and work_hour > 8 else None)
            # 节假日加班小时 如果是节假日等于实际考勤时长
            worksheet.write(row_index, 14, work_hour if work_status == '休息日' and work_hour > 0 else None)
            # 是否有加班餐 是否有加班餐计算方式以工作日工作到晚上21点以后(含21点)及休息日工作超过4小时（含4小时），自动标识为“有”。
            have_jiabancan = is_have_jiabancan(work_status, date, first[k], work_hour)
            worksheet.write(row_index, 15, have_jiabancan)
            # 加班餐数量 等于“是否有加班餐”列如果显示“有”，则将值写为“1”
            worksheet.write(row_index, 16, 1 if have_jiabancan else None)
            worksheet.set_row(row_index, 20)
            row_index += 1
    workbook.close()
    temp.seek(0)
    sio = StringIO(temp.read())
    temp.close()

    return send_file(sio, mimetype='application/octet-stream', attachment_filename=export_name)
