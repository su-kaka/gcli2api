"""
日志路由模块 - 处理 /logs/* 相关的HTTP请求和WebSocket连接
"""

import asyncio
import datetime
import os

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from starlette.websockets import WebSocketState

import config
from log import log
from src.utils import verify_panel_token
from .utils import ConnectionManager


# 创建路由器
router = APIRouter(prefix="/logs", tags=["logs"])

# WebSocket连接管理器
manager = ConnectionManager()


@router.post("/clear")
async def clear_logs(token: str = Depends(verify_panel_token)):
    """清空日志文件"""
    try:
        # 直接使用环境变量获取日志文件路径
        log_file_path = os.getenv("LOG_FILE", "log.txt")

        # 检查日志文件是否存在
        if os.path.exists(log_file_path):
            try:
                # 清空文件内容（保留文件），确保以UTF-8编码写入
                # 使用 with 确保文件正确关闭
                with open(log_file_path, "w", encoding="utf-8") as f:
                    f.write("")
                    f.flush()  # 强制刷新到磁盘
                    # with 退出时会自动关闭文件
                log.info(f"日志文件已清空: {log_file_path}")

                # 通知所有WebSocket连接日志已清空
                await manager.broadcast("--- 日志文件已清空 ---")

                return JSONResponse(
                    content={"message": f"日志文件已清空: {os.path.basename(log_file_path)}"}
                )
            except Exception as e:
                log.error(f"清空日志文件失败: {e}")
                raise HTTPException(status_code=500, detail=f"清空日志文件失败: {str(e)}")
        else:
            return JSONResponse(content={"message": "日志文件不存在"})

    except Exception as e:
        log.error(f"清空日志文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空日志文件失败: {str(e)}")


@router.get("/download")
async def download_logs(token: str = Depends(verify_panel_token)):
    """下载日志文件"""
    try:
        # 直接使用环境变量获取日志文件路径
        log_file_path = os.getenv("LOG_FILE", "log.txt")

        # 检查日志文件是否存在
        if not os.path.exists(log_file_path):
            raise HTTPException(status_code=404, detail="日志文件不存在")

        # 检查文件是否为空
        file_size = os.path.getsize(log_file_path)
        if file_size == 0:
            raise HTTPException(status_code=404, detail="日志文件为空")

        # 生成文件名（包含时间戳）
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gcli2api_logs_{timestamp}.txt"

        log.info(f"下载日志文件: {log_file_path}")

        return FileResponse(
            path=log_file_path,
            filename=filename,
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"下载日志文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载日志文件失败: {str(e)}")


@router.websocket("/stream")
async def websocket_logs(websocket: WebSocket):
    """WebSocket端点，用于实时日志流"""
    # WebSocket 认证: 从查询参数获取 token
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=403, reason="Missing authentication token")
        log.warning("WebSocket连接被拒绝: 缺少认证token")
        return

    # 验证 token
    try:
        panel_password = await config.get_panel_password()
        if token != panel_password:
            await websocket.close(code=403, reason="Invalid authentication token")
            log.warning("WebSocket连接被拒绝: token验证失败")
            return
    except Exception as e:
        await websocket.close(code=1011, reason="Authentication error")
        log.error(f"WebSocket认证过程出错: {e}")
        return

    # 检查连接数限制
    if not await manager.connect(websocket):
        return

    try:
        # 直接使用环境变量获取日志文件路径
        log_file_path = os.getenv("LOG_FILE", "log.txt")

        # 发送初始日志（限制为最后50行，减少内存占用）
        if os.path.exists(log_file_path):
            try:
                # 使用 with 确保文件正确关闭
                with open(log_file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    # 只发送最后50行，减少初始内存消耗
                    for line in lines[-50:]:
                        if line.strip():
                            await websocket.send_text(line.strip())
            except Exception as e:
                await websocket.send_text(f"Error reading log file: {e}")
                log.error(f"WebSocket初始日志读取错误: {e}")

        # 监控日志文件变化
        last_size = os.path.getsize(log_file_path) if os.path.exists(log_file_path) else 0
        max_read_size = 8192  # 限制单次读取大小为8KB，防止大量日志造成内存激增
        check_interval = 2  # 增加检查间隔，减少CPU和I/O开销

        # 创建后台任务监听客户端断开
        # 即使没有日志更新，receive_text() 也能即时感知断开
        async def listen_for_disconnect():
            try:
                while True:
                    await websocket.receive_text()
            except Exception:
                pass

        listener_task = asyncio.create_task(listen_for_disconnect())

        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                # 使用 asyncio.wait 同时等待定时器和断开信号
                # timeout=check_interval 替代了 asyncio.sleep
                done, pending = await asyncio.wait(
                    [listener_task],
                    timeout=check_interval,
                    return_when=asyncio.FIRST_COMPLETED
                )

                # 如果监听任务结束（通常是因为连接断开），则退出循环
                if listener_task in done:
                    break

                if os.path.exists(log_file_path):
                    current_size = os.path.getsize(log_file_path)
                    if current_size > last_size:
                        # 限制读取大小，防止单次读取过多内容
                        read_size = min(current_size - last_size, max_read_size)

                        try:
                            # 使用 with 确保文件正确关闭，即使发生异常
                            with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                                f.seek(last_size)
                                new_content = f.read(read_size)
                                # with 退出时自动关闭文件句柄

                                # 处理编码错误的情况
                                if not new_content:
                                    last_size = current_size
                                    continue

                                # 分行发送，避免发送不完整的行
                                lines = new_content.splitlines(keepends=True)
                                if lines:
                                    # 如果最后一行没有换行符，保留到下次处理
                                    if not lines[-1].endswith("\n") and len(lines) > 1:
                                        # 除了最后一行，其他都发送
                                        for line in lines[:-1]:
                                            if line.strip():
                                                await websocket.send_text(line.rstrip())
                                        # 更新位置，但要退回最后一行的字节数
                                        last_size += len(new_content.encode("utf-8")) - len(
                                            lines[-1].encode("utf-8")
                                        )
                                    else:
                                        # 所有行都发送
                                        for line in lines:
                                            if line.strip():
                                                await websocket.send_text(line.rstrip())
                                        last_size += len(new_content.encode("utf-8"))
                        except UnicodeDecodeError as e:
                            # 遇到编码错误时，跳过这部分内容
                            log.warning(f"WebSocket日志读取编码错误: {e}, 跳过部分内容")
                            last_size = current_size
                        except Exception as e:
                            await websocket.send_text(f"Error reading new content: {e}")
                            # 发生其他错误时，重置文件位置
                            last_size = current_size

                    # 如果文件被截断（如清空日志），重置位置
                    elif current_size < last_size:
                        last_size = 0
                        await websocket.send_text("--- 日志已清空 ---")

        finally:
            # 确保清理监听任务
            if not listener_task.done():
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"WebSocket logs error: {e}")
    finally:
        manager.disconnect(websocket)
