import asyncio
import json
import random
import re
import aiohttp
from typing import Optional, Dict, Any, Union
from aiohttp import ClientSession, ClientResponse, CookieJar
from aiohttp_socks import ProxyConnector
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from src.config.settings import settings

# 模块级单例：UserAgent() 初始化是 CPU 密集操作（解析内置浏览器数据），
# 放在 client.__init__ 里会导致 worker 每个任务都阻塞事件循环一次，
# web 接口出现随机 1 秒级尖刺。随机化发生在 .random 属性调用时，共享实例无副作用
_UA = UserAgent()

class ZMineAPIError(Exception):
    """自定义API异常"""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response
        self.message = message

class ZMineClient:
    """ZMINE网站API客户端"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        proxy_url: Optional[str] = None,
        cookies: Optional[Dict[str, str]] = None,
        token: Optional[str] = None,
        login_key: Optional[str] = None
    ):
        self.base_url = base_url or settings.site_url
        self.proxy_url = proxy_url or settings.proxy_url
        self._is_socks = False
        self.session: Optional[ClientSession] = None
        self.ua = _UA
        self.cookies = cookies or {}
        self.token = token
        self.login_key = login_key
        self.recovery_phrase: Optional[str] = None
        self._user_info: Optional[Dict[str, Any]] = None
        self.last_request_time = 0.0

        # 默认请求头
        self.default_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
            "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": self.ua.random if settings.use_random_ua else "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        }

    async def __aenter__(self) -> "ZMineClient":
        """异步上下文管理器入口"""
        cookie_jar = CookieJar(unsafe=True)
        # SOCKS 代理须经 connector 实现（aiohttp 的 proxy= 参数仅支持 HTTP 代理）
        self._is_socks = bool(self.proxy_url and self.proxy_url.startswith(("socks4://", "socks5://")))
        connector = ProxyConnector.from_url(self.proxy_url) if self._is_socks else None
        self.session = ClientSession(
            base_url=self.base_url,
            cookie_jar=cookie_jar,
            headers=self.default_headers,
            connector=connector
        )

        # 设置初始cookies（站点认证基于 zmine_session cookie，非 Bearer token）
        if self.cookies:
            for name, value in self.cookies.items():
                self.session.cookie_jar.update_cookies({name: value})

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """异步上下文管理器出口"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def _wait_request_delay(self) -> None:
        """请求间隔延迟，避免请求过快"""
        now = asyncio.get_event_loop().time()
        time_since_last = now - self.last_request_time
        min_delay = settings.request_delay_min
        max_delay = settings.request_delay_max

        if time_since_last < min_delay:
            delay = random.uniform(min_delay - time_since_last, max_delay - time_since_last)
            await asyncio.sleep(delay)

        self.last_request_time = asyncio.get_event_loop().time()

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=1, min=settings.retry_delay, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError, ZMineAPIError)),
        reraise=True
    )
    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Union[Dict[str, Any], str]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """通用请求方法"""
        if not self.session:
            raise RuntimeError("Client not initialized. Use async with context manager.")

        await self._wait_request_delay()

        # 合并自定义headers
        request_headers = {}
        if headers:
            request_headers.update(headers)

        try:
            response: ClientResponse = await self.session.request(
                method=method,
                url=path,
                params=params,
                data=data,
                json=json_data,
                headers=request_headers,
                proxy=None if self._is_socks else self.proxy_url,
                timeout=settings.request_timeout,
                **kwargs
            )

            # 处理响应
            if response.status in (200, 201, 204):
                if response.status == 204:
                    return {"success": True}

                try:
                    return await response.json()
                except json.JSONDecodeError:
                    text = await response.text()
                    return {"raw_text": text}

            elif response.status == 401:
                raise ZMineAPIError("Unauthorized", status_code=401)
            elif response.status == 403:
                raise ZMineAPIError("Forbidden", status_code=403)
            elif response.status == 404:
                raise ZMineAPIError("Not Found", status_code=404)
            elif response.status >= 500:
                raise ZMineAPIError(f"Server Error: {response.status}", status_code=response.status)
            else:
                error_text = await response.text()
                raise ZMineAPIError(
                    f"Request failed with status {response.status}: {error_text}",
                    status_code=response.status
                )

        except asyncio.TimeoutError:
            raise ZMineAPIError("Request timeout")
        except aiohttp.ClientError as e:
            raise ZMineAPIError(f"Request error: {str(e)}")

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """GET请求"""
        return await self._request("GET", path, params=params, **kwargs)

    async def post(self, path: str, json_data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """POST请求"""
        return await self._request("POST", path, json_data=json_data, **kwargs)

    async def put(self, path: str, json_data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """PUT请求"""
        return await self._request("PUT", path, json_data=json_data, **kwargs)

    async def delete(self, path: str, **kwargs) -> Dict[str, Any]:
        """DELETE请求"""
        return await self._request("DELETE", path, **kwargs)

    def set_token(self, token: str) -> None:
        """设置认证Token（站点实际使用session cookie，此方法仅记录）"""
        self.token = token

    async def get_exit_ip(self) -> Optional[str]:
        """查询当前会话的代理出口 IP（走同一 connector，与站点请求同 IP），用于风控溯源"""
        try:
            async with self.session.get("https://api.ipify.org?format=json") as resp:
                data = await resp.json(content_type=None)
                return data.get("ip")
        except Exception:
            return None

    def get_cookies_dict(self) -> Dict[str, str]:
        """获取当前所有Cookie的字典形式"""
        if not self.session:
            return self.cookies
        return {cookie.key: cookie.value for cookie in self.session.cookie_jar}

    def update_cookies(self, cookies: Dict[str, str]) -> None:
        """更新Cookie"""
        self.cookies.update(cookies)
        if self.session:
            self.session.cookie_jar.update_cookies(cookies)

    # -------------------------- 业务方法（基于 zmine.si 前端逆向还原） --------------------------
    # 站点为 Next.js 应用，API 前缀 /api/v1，认证基于 zmine_session cookie
    # 错误响应格式: {"error": {"code": "...", "message": "...", "details": [...]}}

    async def register(
        self,
        display_name: str,
        referral_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """注册账号
        POST /api/v1/auth/register {"displayName", "referralCode"?}
        返回：user、loginKey（登录凭证，需保存）、recoveryPhrase（助记词，需保存）
        """
        payload: Dict[str, Any] = {"displayName": display_name}
        if referral_code:
            payload["referralCode"] = referral_code
        response = await self.post("/api/v1/auth/register", json_data=payload)

        # 注册成功后保存登录信息
        self._user_info = response.get("user")
        self.login_key = response.get("loginKey")
        self.recovery_phrase = response.get("recoveryPhrase")

        return response

    async def login(self, login_key: Optional[str] = None) -> Dict[str, Any]:
        """使用loginKey登录账号
        POST /api/v1/auth/login {"loginKey"}
        成功后通过 Set-Cookie 下发 zmine_session（HttpOnly），后续请求靠 cookie 认证
        返回：{"user": {"id", "handle", "role", "displayName"}}
        """
        login_key = login_key or self.login_key
        if not login_key:
            raise ValueError("loginKey is required")

        payload = {
            "loginKey": login_key
        }
        response = await self.post("/api/v1/auth/login", json_data=payload)

        if "user" in response:
            self._user_info = response.get("user")
            self.login_key = login_key

        return response

    async def logout(self) -> Dict[str, Any]:
        """退出登录 POST /api/v1/auth/logout"""
        return await self.post("/api/v1/auth/logout")

    async def get_profile(self) -> Dict[str, Any]:
        """获取用户信息（登录响应中的 user 对象）"""
        if self._user_info:
            return self._user_info
        return {}

    async def get_wallet(self) -> Dict[str, Any]:
        """获取钱包余额
        GET /api/v1/wallet
        返回：balances.MINE/ZEC {available, locked, staked}、stakingReserve、addresses 等
        """
        return await self.get("/api/v1/wallet")

    async def get_mining_status(self) -> Dict[str, Any]:
        """获取挖矿状态
        GET /api/v1/mining/overview
        返回：miner{streakDays,activations,totalMined}、hashpower{baseMh,totalMh}、
              sessionActive、canActivate、last24h、recentRewards、boosts、catalog
        """
        return await self.get("/api/v1/mining/overview")

    async def start_mining(self) -> Dict[str, Any]:
        """开始/续期挖矿会话（24小时）
        POST /api/v1/mining/activate {} （空JSON体）
        返回：miner、hashpower、sessionActive、canActivate 等
        会话激活期间收益按 epoch 自动累计，无需单独领取
        会话已激活时站点返回 409 conflict，视为幂等成功并返回当前状态
        """
        try:
            return await self.post("/api/v1/mining/activate", json_data={})
        except ZMineAPIError as e:
            if e.status_code == 409:
                # 已有激活的挖矿会话，目标状态已达成
                return await self.get_mining_status()
            raise

    async def activate_boost(self, sku: str) -> Dict[str, Any]:
        """购买挖矿加速（消耗ZEC）
        POST /api/v1/mining/boosts {"sku": "spark"|"pulse"|...}
        """
        return await self.post("/api/v1/mining/boosts", json_data={"sku": sku})

    # MINE 代币精度：站点金额均为最小单位（类似 zats），1 MINE = 10^8（前端用 /1e8 格式化）
    TOKEN_DECIMALS = 100_000_000

    async def sync_account_stats(self) -> Dict[str, Any]:
        """同步账号统计数据（挖矿收益自动累计，"领取"实为读取最新统计）
        聚合 GET /api/v1/mining/overview 与 GET /api/v1/wallet
        返回扁平化字段供 worker 更新账号（金额已换算为 MINE 主单位）
        """
        overview = await self.get_mining_status()
        wallet = await self.get_wallet()

        mine_balance = (wallet.get("balances") or {}).get("MINE") or {}
        miner = overview.get("miner") or {}
        hashpower = overview.get("hashpower") or {}

        d = self.TOKEN_DECIMALS
        return {
            "user": self._user_info or {},
            "points": float(mine_balance.get("available") or 0) / d,
            "total_points": float(miner.get("totalMined") or 0) / d,
            "mining_status": 1 if overview.get("sessionActive") else 0,
            "session_expires_at": overview.get("sessionExpiresAt"),
            "can_activate": overview.get("canActivate"),
            "streak_days": miner.get("streakDays"),
            "total_mh": hashpower.get("totalMh"),
            "last_24h": float(overview.get("last24h") or 0) / d,
            "recent_rewards": [
                {**r, "amount": float(r.get("amount") or 0) / d}
                for r in (overview.get("recentRewards") or [])
            ],
        }

    # -------------------------- Mind 答题（/app/si 每日12题，答对 +5000mH 算力24h） --------------------------

    async def get_mind_task(self) -> Dict[str, Any]:
        """获取当前答题任务
        GET /api/v1/si/tasks/next
        返回：{"task": {"id","slug","kind","prompt","options","difficulty","token"} | None,
              "reason": None|"...", "stats": {"remainingToday","cap","cooldownLeftMs",...}}
        task 为 null 表示今日已完成或暂无任务（原因见 reason）
        """
        return await self.get("/api/v1/si/tasks/next")

    async def answer_mind_task(self, token: str, answer: int) -> Dict[str, Any]:
        """提交答案
        POST /api/v1/si/tasks/answer {"token": <题目JWT>, "answer": <选项下标，0-based>}
        返回：{"correct","correctAnswer","tooFast","creditedMh","stats"}
        注意：answer 为 0-based（页面按键是1-based，API是0-based，已实测验证：
        两选项verify题提交 answer=1 表示第二个选项"Incorrect"）
        token 签发后15分钟内有效
        """
        return await self.post(
            "/api/v1/si/tasks/answer",
            json_data={"token": token, "answer": answer}
        )


# -------------------------- Mind 题目本地求解 --------------------------

_NUM_RE = r'\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?'
_WORD_NUM = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'twenty': 20, 'fifty': 50, 'hundred': 100,
    'thousand': 1000, 'dozen': 12, 'half': 0.5,
}


def _parse_num(s: str) -> float:
    return float(s.replace(',', ''))


def _stem(w: str) -> str:
    """极简词干化（复数/时态），用于阅读理解题的词重叠匹配"""
    for suf in ('ing', 'ies', 'ed', 'es', 's'):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[:-len(suf)] + ('y' if suf == 'ies' else '')
    return w


def solve_mind_task(prompt: str, options: list) -> int:
    """本地解答 mind 题目，返回 0-based 选项下标（无法求解时返回 0）

    已知题型（slug 前缀 -> 模板）：
    - gen-mul / arith（verify）：算术断言 "An assistant claims: 35 x 54 = 1,920."
    - gen-zat（verify/choose）：ZEC 与 zatoshi 换算（1 ZEC = 1e8 zatoshi）
    - gen-blocks / gen-epoch（choose）：时间-产出换算
    - gen-pct（choose）：百分比
    - read-*（choose）：阅读理解，按与题干文本的词重叠近似匹配
    注意：答错会触发约10分钟站点冷却，无法求解时宁可保守
    """
    opts = [str(o).strip() for o in (options or [])]
    try:
        idx = _solve_mind(prompt or '', opts)
        return idx if 0 <= idx < len(opts) else 0
    except Exception:
        return 0


def _solve_mind(prompt: str, options: list) -> int:
    text = prompt.replace('×', ' x ').replace('÷', ' / ').replace('−', '-')
    low = text.lower()
    lowers = [o.lower() for o in options]

    # 阅读理解题：与题干文本词重叠最多的选项最可能与文意一致
    if 'text:' in low or 'which is consistent' in low or 'according to the text' in low:
        m = re.search(r"Text:\s*['\"“”]?(.+?)['\"“”]?\s*(?:Which|According|What)", text, re.S)
        body = (m.group(1) if m else text).lower()
        stop = {'the', 'a', 'an', 'of', 'to', 'is', 'are', 'be', 'can', 'until',
                'or', 'and', 'from', 'what', 'you', 'in', 'for', 'on', 'after', 'it'}
        words = {_stem(w.strip('.,!?;:"\'()')) for w in body.split()} - stop
        best, best_score = 0, -1
        for i, o in enumerate(options):
            ow = {_stem(w.strip('.,!?;:"\'()')) for w in o.lower().split()} - stop
            score = len(words & ow)
            if score > best_score:
                best_score, best = score, i
        return best

    # 断言题：先验算术 "A op B = C"，再验单位换算 "A ZEC = B zatoshi"
    if 'correct' in lowers and 'incorrect' in lowers:
        m = re.search(rf'({_NUM_RE})\s*([+\-x*/])\s*({_NUM_RE})\s*=\s*({_NUM_RE})', text)
        if m:
            a, op, b, c = (
                _parse_num(m.group(1)), m.group(2),
                _parse_num(m.group(3)), _parse_num(m.group(4)),
            )
            left = {'+': a + b, '-': a - b, 'x': a * b, '*': a * b,
                    '/': a / b if b else None}.get(op)
            claim = left is not None and abs(left - c) < 0.01
            return lowers.index('correct' if claim else 'incorrect')

        claim_nums = [_parse_num(x) for x in re.findall(_NUM_RE, text)]
        if len(claim_nums) >= 2:
            a, c = claim_nums[0], claim_nums[-1]
            claim = any(
                abs(a * k - c) < 0.01 or abs(c * k - a) < 0.01
                for k in (1e8, 1e6, 1e3, 1024, 86400, 3600, 60, 24, 100)
            )
            return lowers.index('correct' if claim else 'incorrect')
        return 0

    # 数值题：提取题面数字（含英文数字词），按常见运算优先级枚举候选并匹配选项
    targets: Dict[float, int] = {}
    for i, o in enumerate(options):
        if re.fullmatch(rf'{_NUM_RE}(?!\d)', o):
            targets.setdefault(_parse_num(o), i)
    if not targets:
        return 0

    nums = [_parse_num(x) for x in re.findall(_NUM_RE, text)]
    # 英文数字词（one/two...）兜底：题面无数字时直接用，否则仅当真实数字候选全部落空时才参与
    word_nums = [v for w, v in _WORD_NUM.items() if re.search(rf'\b{w}\b', low)]
    base_nums = nums or word_nums
    extra_nums = word_nums if nums else []
    if not base_nums:
        return 0

    def gen(vals: list) -> list:
        out: list = []

        def add(v) -> None:
            if v is not None and abs(v) < 1e15:
                out.append(v)

        # 1) ZEC<->zatoshi 换算类（1 ZEC = 1e8 zatoshi）
        if 'zat' in low or 'zec' in low:
            for a in vals:
                add(a * 1e8)
                add(a / 1e8)
                add(a * 1e6)
                add(a / 1e6)
        # 2) 速率->总量换算（每 X 秒/分钟产出 Y，问一天/一小时多少个）
        for b in vals:
            if b:
                add(86400 / b)
                add(3600 / b)
                add(60 / b)
        # 3) 乘积及其换算（epoch 类：块数x间隔 -> 时长）；跳过自配对避免平方项撞干扰项
        for i, a in enumerate(vals):
            for j, b in enumerate(vals):
                if i == j:
                    continue
                p = a * b
                add(p)
                add(p / 60)
                add(p / 3600)
                add(p / 86400)
                add(p * 24)
        # 4) 单数倍增/换算
        for a in vals:
            add(a * 24)
            add(a * 60)
            add(a * 3600)
            add(a * 86400)
            if a:
                add(a / 24)
                add(a / 60)
                add(a / 3600)
        # 5) 比值与和差
        for a in vals:
            for b in vals:
                if b:
                    add(a / b)
                    add(b / a)
                add(a + b)
                add(a - b)
                add(b - a)
        # 6) 倍数/百分比类
        for a in vals:
            if 'half' in low or 'halv' in low:
                add(a / 2)
            if 'double' in low or 'twice' in low:
                add(a * 2)
            if '%' in text:
                for x in vals:
                    add(a * x / 100)
                    add(a * (1 - x / 100))
                    add(a * (1 + x / 100))
        return out

    candidates = gen(base_nums) + (gen(base_nums + extra_nums) if extra_nums else [])

    for v in candidates:
        for tv, idx in targets.items():
            if abs(v - tv) < 0.01:
                return idx
    return 0
