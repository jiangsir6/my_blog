from django import forms
from django.forms import fields


class UserRegisterForm(forms.Form):
    username = forms.CharField(max_length=10, min_length=1,
                               error_messages={'required': '请填写用户名', 'max_length': '用户名不能超过10位',
                                               'min_length': '用户名不能少于1位'})
    password = forms.CharField(max_length=128, min_length=6,
                               error_messages={'required': '请填写密码', 'max_length': '密码不能超过128位',
                                               'min_length': '密码不能少于6位'})
    password1 = forms.CharField(max_length=128, min_length=6,
                                error_messages={'required': '请确认密码', 'max_length': '密码不能超过128位', 'min_length': ''})


class UserLoginForm(forms.Form):
    username = fields.CharField(max_length=18, min_length=2,
                                error_messages={'required': '用户名必须填写', 'max_length': '长度最长18位',
                                                'min_length': '用户名不能少于2位'})
    password = fields.CharField(max_length=18, min_length=6,
                                error_messages={'required': '密码必须填写', 'max_length': '长度最长18位',
                                                'min_length': '密码不能少于6位'})
    checkcode = fields.CharField(error_messages={'required': '验证码必须填写'})


class MessageBoard(forms.Form):
    pass
