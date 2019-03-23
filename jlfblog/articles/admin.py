from django.contrib import admin
from articles.models import Category,ArticleInfo
# Register your models here.

class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name','add_time']

class ArticleInfoAdmin(admin.ModelAdmin):
    list_display = ['title','desc','content','comment_num','click_num','add_time','category','author']


admin.site.register(Category,CategoryAdmin)
admin.site.register(ArticleInfo,ArticleInfoAdmin)