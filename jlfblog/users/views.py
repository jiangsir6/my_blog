from django.shortcuts import render, redirect, reverse, HttpResponse
from users.forms import UserRegisterForm, UserLoginForm
from users.models import UserProfile
from articles.models import ArticleInfo, Category
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth import login, logout
import random
from django.db.models import Count
from PIL import Image, ImageDraw, ImageFont
from django.core.mail import send_mail


# Create your views here.


def index(request):
    # 获取所有文章
    articles = ArticleInfo.objects.all()
    # 获得点击量前三的作为热门文章
    click_order = articles.order_by('-click_num')[:3]
    # 获得所有标签
    categorys = Category.objects.all()
    # 统计所有分类的文章个数
    c1 = ArticleInfo.objects.filter(category=1).aggregate(Count('id'))['id__count']
    c2 = ArticleInfo.objects.filter(category=2).aggregate(Count('id'))['id__count']
    c3 = ArticleInfo.objects.filter(category=3).aggregate(Count('id'))['id__count']
    c4 = ArticleInfo.objects.filter(category=4).aggregate(Count('id'))['id__count']
    c5 = ArticleInfo.objects.filter(category=5).aggregate(Count('id'))['id__count']
    c6 = ArticleInfo.objects.filter(category=6).aggregate(Count('id'))['id__count']
    # 处理留言
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        message = request.POST.get('message')

        send_mail('感谢您的留言', '您的留言我已收到，看到留言后我会第一时间回复您', 'jlfisgood@163.com',
                  [email], fail_silently=False)

        send_mail('有新的留言', '{}给你留言：{}'.format(name, message), 'jlfisgood@163.com',
                  ['18561699217@163.com'], fail_silently=False)

    return render(request, 'index.html', {
        'click_order': click_order, 'categorys': categorys, 'c1': c1, 'c2': c2, 'c3': c3, 'c4': c4, 'c5': c5, 'c6': c6})


# 用户登录
def user_login(request):
    if request.method == 'GET':
        return render(request, 'user_login.html')

    else:
        # 获取表单信息
        user_login_form = UserLoginForm(request.POST)
        # 判断表单信息
        if user_login_form.is_valid():
            username = user_login_form.cleaned_data['username']
            password = user_login_form.cleaned_data['password']
            checkcode = user_login_form.cleaned_data['checkcode']

            user = UserProfile.objects.filter(username=username)
            # 判断用户是否注册
            if user.exists():
                user = user.first()
                code = request.session['code']
                # 密码核对
                if code.lower() == checkcode.lower():
                    if user:
                        if check_password(password, user.password):
                            request.user = user
                            request.session['username'] = username
                            return redirect(reverse('index'))

                        else:
                            return render(request, 'user_login.html', {'msg': '密码错误！'})
                else:
                    return render(request, 'user_login.html', {'msg': '验证码错误'})

            else:
                return render(request, 'user_login.html', {'msg': '该用户不存在,请确认再登录'})


# 用户注册
def user_register(request):
    if request.method == 'GET':
        return render(request, 'user_register.html')

    else:
        user_reform = UserRegisterForm(request.POST)
        if user_reform.is_valid():
            username = user_reform.cleaned_data['username']
            password = user_reform.cleaned_data['password']
            password1 = user_reform.cleaned_data['password1']

            user = UserProfile.objects.filter(username=username)
            if user:
                return render(request, 'user_register.html', {'msg': '该用户名已存在'})

            else:
                if password == password1:
                    userprofile = UserProfile()
                    userprofile.username = username
                    hash_password = make_password(password)
                    userprofile.password = hash_password
                    userprofile.save()
                    return redirect(reverse('users:user_login'))

                else:
                    return render(request, 'user_register.html', {'msg': '两次输入的密码不一致'})

        else:
            print(user_reform.errors)
            return render(request, 'user_register.html', {'user_reform': user_reform})


# 用户登出
def user_logout(request):
    logout(request)
    return redirect('index')


def get_color():
    red = random.randrange(255)
    green = random.randrange(255)
    blue = random.randrange(255)

    return (red, green, blue)


# 生成验证码
def get_code(request):
    size = (100, 40)
    color = get_color()
    image = Image.new('RGB', size, color)

    image_draw = ImageDraw.Draw(image, 'RGB')

    image_font = ImageFont.truetype('/root/blog/ARIALBI.TTF', 23)
    source = 'qwertyuiopasdfghjklzxcvbnm1234567890QWERTYUIOPPASDFGHJKLZXCVBNM'
    code = ''

    for i in range(4):
        code += source[random.randrange(len(source))]
    # 放入session中
    request.session['code'] = code

    for i in range(len(code)):
        image_draw.text((10 + 18 * i, 10), code[i], fill=get_color(), font=image_font)

    # 干扰点
    for i in range(500):
        image_draw.point((random.randrange(100), random.randrange(40)), fill=get_color())

    import io
    buf = io.BytesIO()
    image.save(buf, 'png')

    return HttpResponse(buf.getvalue(), 'image/png')
