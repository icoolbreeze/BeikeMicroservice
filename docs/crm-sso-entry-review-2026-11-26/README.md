# CRM现有凭证可复用入口核实

核实日期：2026-11-26（America/Los_Angeles；请求发生于UTC 2026-11-27）。范围为当前已授权员工账号，从实际工作台菜单、房源页面、员工门户和既有项目记录中发现的入口。不是集团所有系统的全量清单。

## 核实结果

- 候选主机入口：45 个；认证通过的原始入口：30 个。
- 按最终到达主机合并重定向和别名后：28 个认证入口。其中 8 个核对了身份接口，另 20 个完成私有页面门禁／SSO对照验证。
- 排除现有买卖、普通租赁、托管三个核心工作台后：25 个其他认证入口。
- 本机浏览器未使用本轮SSH SOCKS通道、直接验证成功：5 个。其余入口通过云端现有办公网络验证，未据此断言它们都能在本机直连。

A＝身份接口返回成功且主体与现有CRM凭证一致；B＝正常SSO／私有页面门禁通过，未完成全部业务接口权限验收。认证通过不表示该系统全部功能均有权限。

## 已确认的认证入口

| 入口 | 实际到达地址 | 证据等级 | 网络观察 |
| --- | --- | --- | --- |
| 客服中心／业绩争议 | [helper.ke.com](https://helper.ke.com/) | A | 本机直连已验证 |
| 商铺写字楼（新版房源列表） | [listing-de.ke.com](https://listing-de.ke.com/pc/de/list) | A | 本机直连已验证 |
| 豌豆 | [wandou.ke.com](https://wandou.ke.com/chat) | A | 本机直连已验证 |
| AI门户 | [letsai.ke.com](https://letsai.ke.com/) | B | 本机直连已验证 |
| Moma主站 | [moma.ke.com](https://moma.ke.com/nchat) | A | 本机直连已验证 |
| 二手自然商机展位 | [naturaloppty.ke.com](https://naturaloppty.ke.com/) | B | 云端网络已验证 |
| 工作台首页 | [shouye.link.lianjia.com](http://shouye.link.lianjia.com/shou/index) | B | 云端网络已验证 |
| 共享池（旧客源入口） | [ke.link.lianjia.com](https://ke.link.lianjia.com/customer/sharePool/page/list) | B | 云端网络已验证 |
| 管理驾驶舱 | [odin.ke.com](https://odin.ke.com/portal/1809) | A | 云端网络已验证 |
| 积分提佣 | [8.lianjia.com](http://8.lianjia.com/) | B | 云端网络已验证 |
| 金贝展位推广平台 | [brand.ke.com](https://brand.ke.com/) | B | 云端网络已验证 |
| 考勤 | [kaoqin.hr.ke.com](http://kaoqin.hr.ke.com/) | B | 云端网络已验证 |
| 客户端通知中心／IM | [kemcc.ke.com](https://kemcc.ke.com/noticeCenter) | A | 云端网络已验证 |
| 客源 | [ke.deyoulife.com](https://ke.deyoulife.com/customer-infrastructure/customer/page/allCustomer) | B | 云端网络已验证 |
| 买卖房源／合同下载／钥匙管理 | [house.link.lianjia.com](https://house.link.lianjia.com/search/sale/default/gdiv_mt) | B | 云端网络已验证 |
| 培训 | [study.ke.com](https://study.ke.com/) | B | 云端网络已验证 |
| 普通租赁 | [lease-pz.link.lianjia.com](https://lease-pz.link.lianjia.com/rent/house/list) | B | 云端网络已验证 |
| 签约 | [hetong.link.lianjia.com](https://hetong.link.lianjia.com//index) | B | 云端网络已验证 |
| 商圈房源聚焦 | [newhouse-cps.ke.com](https://newhouse-cps.ke.com/grid/focus/admineActivity) | B | 云端网络已验证 |
| 省心租托管 | [trusteeship.link.lianjia.com](https://trusteeship.link.lianjia.com/) | B | 云端网络已验证 |
| 我的潜在客户 / 客户公海 | [clue.a.ke.com](https://clue.a.ke.com/page/potential/customer/list) | B | 云端网络已验证 |
| 消息 | [xiaoxi.link.lianjia.com](https://xiaoxi.link.lianjia.com//trend/index) | B | 云端网络已验证 |
| 新房 | [link.fang.lianjia.com](https://link.fang.lianjia.com/map) | A | 云端网络已验证 |
| 薪酬 | [xinchou.ke.com](https://xinchou.ke.com/) | B | 云端网络已验证 |
| 员工门户／个人档案 | [family.ke.com](https://family.ke.com/myInfo/profile) | A | 云端网络已验证 |
| 展位管理后台 | [liulian.ke.com](http://liulian.ke.com/static/buy/commentExpo) | B | 云端网络已验证 |
| A+客户端认证服务 | [saas.a.ke.com](http://saas.a.ke.com/) | B | 云端网络已验证 |
| HR门户 | [hr.ke.com](http://hr.ke.com/) | B | 云端网络已验证 |

## 消息相关新增实测

- https://kemcc.ke.com/noticeCenter ：通知中心登录后自动建立IM会话；https://imapi.lianjia.com/user/detail/fetch 返回HTTP 200、业务码0，用户主体与CRM一致。
- /user/conv/list 与 /msg/sync 返回HTTP 200、业务码0。本次该入口返回的会话和消息列表均为0条，只代表当前入口的可见范围，不能推断账号在所有业务中的聊天记录均为空。
- 通知中心的角色接口返回空roles列表，不据此认定具有通知管理或发送权限。本轮没有实发消息。
- 通知中心本机直接访问出现连接关闭；其登录及IM接口成功结果来自云端办公网络。
- https://xiaoxi.link.lianjia.com/ 是动态／系统消息页面。aplus-im.ke.com 本轮云端DNS解析失败，未计入可用入口。

## 本机直接可用的入口

| 入口 | 认证／业务证据 |
| --- | --- |
| [客服中心／业绩争议](https://helper.ke.com/) | 身份接口主体一致 |
| [商铺写字楼（新版房源列表）](https://listing-de.ke.com/pc/de/list) | 身份接口主体一致；/api/user 与房源列表业务码100000，页面自动加载列表10条 |
| [豌豆](https://wandou.ke.com/chat) | 身份接口主体一致；在售房源查询成功，最小样本1条 |
| [AI门户](https://letsai.ke.com/) | 通过SSO，认证后的页面包含当前主体标识 |
| [Moma主站](https://moma.ke.com/nchat) | 身份接口主体一致 |

## 未计入可用统计的入口

| 入口 | 地址 | 本轮结果 |
| --- | --- | --- |
| 喜刷刷 | [xss.lianjia.com](http://xss.lianjia.com) | 网络、证书或外部跳转受阻；RemoteDisconnected:Remote end closed connection without response |
| 签后 | [i.nts.jiaoyi.ke.com](http://i.nts.jiaoyi.ke.com) | SSO后权限／Forbidden页；HTTP 403；无权限 |
| 加油站商城 | [jiayou.lianjia.com](http://jiayou.lianjia.com) | 网络、证书或外部跳转受阻；RuntimeError:external_redirect:www.jiayou321.com |
| 楼盘字典 | [reds.lianjia.com](http://reds.lianjia.com) | 仍要求登录；HTTP 200；登录页面 |
| 帮住专家 | [rfq.ke.com](http://rfq.ke.com/helpexpert/list) | SSO完成，但业务落到403页；HTTP 200；帮住专家 |
| TE | [te.lianjia.com](http://te.lianjia.com) | HTTP业务故障；HTTP 502；502 Bad Gateway |
| 链家邮箱 | [mail.homelink.com.cn](https://mail.homelink.com.cn) | 网络、证书或外部跳转受阻；URLError:<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'mail.homelink.com.cn'. (_ssl.c:1000)> |
| 图灵 | [databoard.lianjia.com](https://databoard.lianjia.com) | 网络、证书或外部跳转受阻；URLError:<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1000)> |
| 店面管理系统 | [sm.lianjia.com](http://sm.lianjia.com) | 仍要求登录；HTTP 302 |
| IM WebView | [aplus-im.ke.com](https://aplus-im.ke.com/) | 网络、证书或外部跳转受阻；URLError:<urlopen error [Errno -2] Name or service not known> |
| Moma文案App | [app-moma.ke.com](https://app-moma.ke.com/60758c5d-5f65-47aa-8238-645d8caaffb1/#/) | 仅页面可达，未确认登录；HTTP 200；视频号/小红书文案生成器 - 贝壳房源AI新媒体文案工具 |
| 小区 | [hestia.lianjia.com](https://hestia.lianjia.com/districtList?from=request_from_A_plus_resblock) | SSO回调后，浏览器最终到达Forbidden/权限页；HTTP 200；小区列表 |
| 绩效 | [jixiao.ke.com](http://jixiao.ke.com/p/home) | 仅页面可达，未确认登录；HTTP 200；职能绩效系统 |
| 员工文化应用 | [app-fly.ke.com](https://app-fly.ke.com/4ca0a304-9d83-458e-8355-e4bcc1494196) | 仅页面可达，未确认登录；HTTP 200；贝壳企业文化数字平台 |
| 链家产品门户 | [lj.lianjia.com](https://lj.lianjia.com/page/home) | 仍要求登录；HTTP 200；登录 |

## 认证方式与验收边界

多数入口使用保存的TGC经厂商正常CAS换取各自应用会话；已有房源业务Cookie用于原业务域。豌豆使用当前有效的CRM普租令牌作为其前端原生Bearer凭证，身份与房源查询已实际验证。没有伪造用户、角色或管理员状态。

楼盘字典旧入口 reds.lianjia.com 仍要求登录；新小区入口 hestia.lianjia.com 虽然经过SSO并返回过小区列表页面，执行前端后最终到达 hip.lianjia.com/forbidden，已剔除。签后交易与帮住专家同样出现权限页，不计入业务可用。

HTTP 200、出现应用标题、自动跳转到某域名均不单独算作登录成功。Moma文案App、绩效和员工文化应用本轮未确认当前身份，不将历史结论充当本次通过结果。此处未追踪集团全部目录、未枚举编号、未导出员工或房源数据；证据只保留状态、字段名、条数和主体一致性。

## 脱敏证据文件

- evidence.json：候选入口的匿名／带凭证页面对照。
- public-app-browser.json：Moma、豌豆与文案App浏览器复核。
- focused-browser.json：通知中心IM、奥丁、员工门户、新房与小区入口复核。
- direct-browser.json：本机直接访问复核。
- commercial-browser.json：商铺写字楼身份与列表最小验证。
- summary.json：最终分组和统计。

以上记录不包含Cookie、TGC、Bearer、临时service ticket、原始个人资料或消息正文。没有修改生产认证、路由白名单、归档流程或服务部署。
