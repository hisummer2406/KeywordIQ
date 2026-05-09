# app/admin/admin_site.py - 简单修复版本
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi_amis_admin import admin
from fastapi_amis_admin.amis.components import App, Tpl
from fastapi_amis_admin.amis.components import Page
from fastapi_amis_admin.admin import Settings, AdminSite

from config import settings


ADMIN_SIDEBAR_COLLAPSED_CSS = """
<style>
    .admin-sidebar-collapsed [class*="App-aside"],
    .admin-sidebar-collapsed [class*="Layout-aside"] {
        width: 56px !important;
        min-width: 56px !important;
        max-width: 56px !important;
        flex: 0 0 56px !important;
        flex-basis: 56px !important;
        overflow-x: hidden !important;
        transition: width 0.2s ease, min-width 0.2s ease, max-width 0.2s ease;
    }

    .admin-sidebar-collapsed [class*="App-aside"]:hover,
    .admin-sidebar-collapsed [class*="Layout-aside"]:hover {
        width: 220px !important;
        min-width: 220px !important;
        max-width: 220px !important;
        flex: 0 0 220px !important;
        flex-basis: 220px !important;
    }

    .admin-sidebar-collapsed [class*="App-aside"] [class*="brandText"],
    .admin-sidebar-collapsed [class*="App-aside"] [class*="AppBcn"],
    .admin-sidebar-collapsed [class*="App-aside"] [class*="AsideNav-itemLabel"],
    .admin-sidebar-collapsed [class*="App-aside"] [class*="AsideNav-label"],
    .admin-sidebar-collapsed [class*="App-aside"] [class*="Nav-itemLabel"],
    .admin-sidebar-collapsed [class*="App-aside"] [class*="Nav-label"],
    .admin-sidebar-collapsed [class*="Layout-aside"] [class*="brandText"],
    .admin-sidebar-collapsed [class*="Layout-aside"] [class*="AppBcn"],
    .admin-sidebar-collapsed [class*="Layout-aside"] [class*="AsideNav-itemLabel"],
    .admin-sidebar-collapsed [class*="Layout-aside"] [class*="AsideNav-label"],
    .admin-sidebar-collapsed [class*="Layout-aside"] [class*="Nav-itemLabel"],
    .admin-sidebar-collapsed [class*="Layout-aside"] [class*="Nav-label"] {
        opacity: 0 !important;
        white-space: nowrap !important;
        transition: opacity 0.15s ease;
    }

    .admin-sidebar-collapsed [class*="App-aside"]:hover [class*="brandText"],
    .admin-sidebar-collapsed [class*="App-aside"]:hover [class*="AppBcn"],
    .admin-sidebar-collapsed [class*="App-aside"]:hover [class*="AsideNav-itemLabel"],
    .admin-sidebar-collapsed [class*="App-aside"]:hover [class*="AsideNav-label"],
    .admin-sidebar-collapsed [class*="App-aside"]:hover [class*="Nav-itemLabel"],
    .admin-sidebar-collapsed [class*="App-aside"]:hover [class*="Nav-label"],
    .admin-sidebar-collapsed [class*="Layout-aside"]:hover [class*="brandText"],
    .admin-sidebar-collapsed [class*="Layout-aside"]:hover [class*="AppBcn"],
    .admin-sidebar-collapsed [class*="Layout-aside"]:hover [class*="AsideNav-itemLabel"],
    .admin-sidebar-collapsed [class*="Layout-aside"]:hover [class*="AsideNav-label"],
    .admin-sidebar-collapsed [class*="Layout-aside"]:hover [class*="Nav-itemLabel"],
    .admin-sidebar-collapsed [class*="Layout-aside"]:hover [class*="Nav-label"] {
        opacity: 1 !important;
    }
</style>
"""


class CustomAdminSite(AdminSite):
    """自定义管理站点"""

    async def page_parser(self, request: Request, page: Page):
        """Inject global admin-shell styles into the final HTML page."""
        response = await super().page_parser(request, page)
        if request.method != "GET" or not isinstance(response, HTMLResponse):
            return response

        html = response.body.decode(response.charset or "utf-8")
        if "admin-sidebar-collapsed" in html and ADMIN_SIDEBAR_COLLAPSED_CSS not in html:
            html = html.replace(
                "<body>",
                '<body class="admin-sidebar-collapsed">',
                1,
            )
            html = html.replace("</head>", f"{ADMIN_SIDEBAR_COLLAPSED_CSS}</head>")
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return HTMLResponse(
                content=html,
                status_code=response.status_code,
                headers=headers,
            )

        return response

    async def _get_page_as_app1(self, request: Request) -> App:
        """自定义应用页面"""
        app = App()
        app.brandName = self.site.settings.site_title
        app.logo = self.site.settings.site_icon

        # 添加用户信息和登出功能
        app.header = Tpl(
            className="w-full",
            tpl='''<div class="flex justify-between">
                                   <div class="header-left"></div>
                                   <div class="header-right"></div>
                </div>''',
        )
        app.footer = ''

        children = await self.get_page_schema_children(request)
        app.pages = [{'children': children}] if children else []
        return app


    async def _get_page_as_app(self, request: Request) -> App:
        """自定义应用页面 - 添加认证逻辑"""
        app = App()
        app.brandName = self.site.settings.site_title
        app.logo = self.site.settings.site_icon
        app.className = "admin-shell admin-sidebar-collapsed"

        # 添加全局请求拦截器，自动添加Authorization头
        app.requestAdaptor = '''
            const token = localStorage.getItem('access_token');
            if (token && api.url.indexOf('/api/') !== -1) {
                api.headers = api.headers || {};
                api.headers['Authorization'] = 'Bearer ' + token;
            }
            return api;
        '''

        # 添加响应拦截器，处理401错误
        app.responseAdaptor = '''
            if (payload.status === 401) {
                localStorage.removeItem('access_token');
                window.location.href = '/admin/login';
                return payload;
            }
            return payload;
        '''

        # 修复header显示 - 使用JavaScript获取用户名
        app.header = {
            "type": "container",
            "className": "w-full bg-white shadow-sm px-4 py-1",
            "body": [
                {
                    "type": "flex",
                    "justify": "space-between",
                    "alignItems": "center",
                    "items": [
                        {
                            "type": "service",
                            "api": {
                                "method": "get",
                                "url": "/api/auth/profile",
                                "headers": {
                                    "Authorization": "${ls:access_token ? 'Bearer ' + ls:access_token : ''}"
                                }
                            },
                            "body": {
                                "type": "tpl",
                                "tpl": "<h4 class='m-0'>欢迎，${username}</h4>"
                            }
                        },
                        {
                            "type": "button",
                            "label": "退出登录",
                            "level": "link",
                            "size": "sm",
                            "onEvent": {
                                "click": {
                                    "actions": [
                                        {
                                            "actionType": "custom",
                                            "script": """
                                                                localStorage.removeItem('access_token');
                                                                window.location.href = '/admin/login';
                                                            """
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                }
            ]
        }

        app.footer = {
            "type": "container",
            "className": "text-center py-3 bg-gray-50 border-t",
            "body": [
                {
                    "type": "divider",
                    "className": "my-2"
                },
                {
                    "type": "tpl",
                    "tpl": "© 2025 亚马逊数据分析系统 v1.0 | 专业·高效·智能",
                    "className": "text-xs text-gray-500"
                },
                {
                    "type": "tpl",
                    "tpl": "🔥技术支持: stone_summer24 | 数据驱动商业洞察",
                    "className": "text-xs text-gray-400 mt-1"
                }
            ]
        }


        children = await self.get_page_schema_children(request)
        app.pages = [{'children': children}] if children else []
        return app

# 初始化站点
site = CustomAdminSite(
    settings=Settings(
        site_title=settings.APP_NAME,
        debug=settings.DEBUG,
        site_icon="/static/amazon_logo.png",
        database_url_async=settings.DATABASE_URL_ASYNC,
        site_url="/",
    )
)

# 取消注册默认管理模块
site.unregister_admin(admin.HomeAdmin)
site.unregister_admin(admin.FileAdmin)

# 注册模块
from app.table.analysis_admin import AmazonDataQueryAdmin
from app.user.user_admin import UserManagementAdmin
# from app.admin.auth.login_admin import LoginAdmin

# site.register_admin(LoginAdmin)
site.register_admin(AmazonDataQueryAdmin)
site.register_admin(UserManagementAdmin)
