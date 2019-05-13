from django.urls import path
from users.views import user_login,user_register,get_code,user_logout

app_name = 'users'
urlpatterns = [
    path('login/',user_login,name='user_login'),
    path('register/',user_register,name='user_register'),
    path('getcode/',get_code,name='get_code'),
    path('logout/',user_logout,name='user_logout'),
]
