from django.urls import path
from articles.views import *

app_name = 'articles'
urlpatterns = [
        path('category/<int:categoryid>',categorys,name='category'),
        path('articledetails/<int:articleid>',get_article,name='article_details'),
        path('add_comment/<int:articleid>',add_comment,name='add_comment')
]