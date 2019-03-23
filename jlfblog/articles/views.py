from django.shortcuts import render,redirect,reverse
from articles.models import Category,ArticleInfo,CommentInfo
from users.models import UserProfile
# Create your views here.


def categorys(request,categoryid):

    category = Category.objects.get(pk=categoryid)
    articles = ArticleInfo.objects.filter(category=categoryid)


    return render(request,'article_list.html',{'articles':articles,'category':category})


def get_article(request,articleid):

    article = ArticleInfo.objects.get(pk=articleid)
    comments = CommentInfo.objects.filter(comment_article=articleid)
    article.click_num += 1
    article.save()


    return render(request,'article_details.html',{'article':article,'comments':comments})



def add_comment(request,articleid):

    article = ArticleInfo.objects.get(pk=articleid)
    comment = request.POST.get('comment')
    person = UserProfile.objects.get(username=request.session['username'])


    comment_obj=CommentInfo()
    comment_obj.comment_person = person
    comment_obj.comment_article = article
    comment_obj.comment_content = comment
    comment_obj.save()

    #评论数+1
    article.comment_num += 1
    article.save()

    return redirect(reverse('articles:article_details',args=[articleid]))