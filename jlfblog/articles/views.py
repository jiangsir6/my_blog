from django.shortcuts import render, redirect, reverse
from articles.models import Category, ArticleInfo, CommentInfo
from users.models import UserProfile
from markdown import markdown
from django.db.models import Q
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage


# Create your views here.


def categorys(request, categoryid):
    category = Category.objects.get(pk=categoryid)
    articles = ArticleInfo.objects.filter(category=categoryid)

    # 分页
    paginator = Paginator(articles, 3)

    try:
        num = request.GET.get('page', 1)
        number = paginator.page(num)
    # 如果不是整数则显示第一页的内容
    except PageNotAnInteger:
        number = paginator.page(1)
    except EmptyPage:
        number = paginator.page(paginator.num_pages)

    return render(request, 'article_list.html', {'articles': number, 'category': category, 'num': num})


def get_article(request, articleid):
    article = ArticleInfo.objects.get(pk=articleid)
    comments = CommentInfo.objects.filter(comment_article=articleid)
    article_content = markdown(article.content,
                               extensions=[
                                   # 包含 缩写、表格等常用扩展
                                   'markdown.extensions.extra',
                                   # 语法高亮扩展
                                   'markdown.extensions.codehilite',
                               ])
    article.click_num += 1
    article.save()

    return render(request, 'article_details.html',
                  {'article': article, 'comments': comments, 'article_content': article_content})


def add_comment(request, articleid):
    article = ArticleInfo.objects.get(pk=articleid)
    comment = request.POST.get('comment')
    person = UserProfile.objects.get(username=request.session['username'])

    comment_obj = CommentInfo()
    comment_obj.comment_person = person
    comment_obj.comment_article = article
    comment_obj.comment_content = comment
    comment_obj.save()

    # 评论数+1
    article.comment_num += 1
    article.save()

    return redirect(reverse('articles:article_details', args=[articleid]))


def search_article(request):
    # 获取关键词
    keyword = request.GET.get('search')
    # 获取包含关键词的文章
    keyword_article = ArticleInfo.objects.filter(Q(title__icontains=keyword) | Q(desc__icontains=keyword))

    # 分页
    paginator = Paginator(keyword_article, 3)

    try:
        num = request.GET.get('page', 1)
        number = paginator.page(num)
    # 如果不是整数则显示第一页的内容
    except PageNotAnInteger:
        number = paginator.page(1)
    except EmptyPage:
        number = paginator.page(paginator.num_pages)

    return render(request, 'search_article_list.html', {'keyword': keyword, 'articles': number, 'num': num})
