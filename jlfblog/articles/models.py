from django.db import models
from datetime import datetime
from users.models import UserProfile
# Create your models here.


class Category(models.Model):

    name = models.CharField(max_length=10,unique=True,verbose_name='文章类别')
    add_time = models.DateTimeField(default=datetime.now,verbose_name='添加时间')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '文章分类'
        verbose_name_plural = verbose_name


class ArticleInfo(models.Model):

    title = models.CharField(max_length=128,verbose_name='文章标题')
    desc = models.CharField(max_length=256,verbose_name='文章简介')
    content = models.TextField(verbose_name='文章内容')
    comment_num = models.IntegerField(verbose_name='评论数',default=0)
    click_num = models.IntegerField(verbose_name='点击数',default=0)
    add_time = models.DateTimeField(default=datetime.now,verbose_name='添加时间')
    author = models.ForeignKey(UserProfile,on_delete=models.CASCADE,verbose_name='文章作者')
    category = models.ForeignKey(Category,on_delete=models.CASCADE,verbose_name='文章类别')

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = '文章详情'
        verbose_name_plural = verbose_name


class CommentInfo(models.Model):

    comment_person = models.ForeignKey(UserProfile,on_delete=models.CASCADE,verbose_name='评论人')
    comment_article = models.ForeignKey(ArticleInfo,on_delete=models.CASCADE,verbose_name='评论文章')
    comment_content = models.TextField(verbose_name='评论内容')
    comment_time = models.DateTimeField(default=datetime.now,verbose_name='评论时间')

    def __str__(self):
        return self.comment_person

    class Meta:
        verbose_name = '文章评论表'
        verbose_name_plural = verbose_name

