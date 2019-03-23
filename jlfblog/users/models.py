from django.db import models

# Create your models here.

class UserProfile(models.Model):

    username = models.CharField(max_length=10,verbose_name='用户名',null=False,unique=True)
    password = models.CharField(max_length=128,verbose_name='密码',null=False)

    def __str__(self):
        return self.username

    class Meta:
        verbose_name = '用户信息'
        verbose_name_plural  = verbose_name

    @classmethod
    def set_password(cls, password):
        pass

