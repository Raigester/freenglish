import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

from userroom.consumers.room_commands import RoomCommands
from userroom.services.room_service import RoomService
from userroom.services.user_service import UserService
from userroom.tasks import deactivate_room_if_empty

logger = logging.getLogger('freenglish')


class RoomConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.commands = RoomCommands(self)
        self.user_service = UserService()
        self.room_service = RoomService()
        self.room_id = None

    async def connect(self):
        self.room_id = self.scope['url_route']['kwargs'].get('room_id')

        if await self.room_exists(self.room_id):
            await self.accept()
        else:
            await self.close()
            logger.warning(f'Tried to connect to non-existent room {self.room_id}')

    async def disconnect(self, close_code):  # noqa: ARG002
        if self.room_id and self.user:
            await self.commands.handle_leave_room(self.room_id, self.user)
            room = await self.room_service.get_room(self.room_id)
            if room:
                participant_count = await self.room_service.count_participants(room)
                logger.info(f"In room {self.room_id} remaining participants: {participant_count}")

                if participant_count == 0:
                    logger.info(f"Room {self.room_id} is empty. Starting the deactivation task.")

                    deactivate_room_if_empty.apply_async((self.room_id,), countdown=15)
                    logger.info(f"The task of deactivating the room {self.room_id} added to the queue.")

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            pass
        if text_data is not None:
            try:
                text_data_json = json.loads(text_data)

                token = text_data_json.get('token')
                if token:
                    self.user = await self.user_service.get_user_from_token(token)
                    if not self.user:
                        await self.send(text_data=json.dumps({'type': 'error', 'message': 'Invalid token.'}))
                        return

                message_type = text_data_json.get('type')
                data = text_data_json.get('data', {})

                if message_type == 'joinRoom':
                    if await self.room_exists(self.room_id):
                        await self.commands.handle_join_room(self.room_id, user=self.user)
                    else:
                        await self.send(text_data=json.dumps({
                            'type': 'error',
                            'message': 'Room does not exist.'
                        }))
                elif message_type == 'leaveRoom':
                    await self.commands.handle_leave_room(self.room_id, user=self.user)
                elif message_type == 'editRoom':
                    await self.commands.handle_edit_room(self.room_id, user=self.user, data=data)
                else:
                    await self.send(text_data=json.dumps({'type': 'error', 'message': 'Unknown message type'}))

            except json.JSONDecodeError:
                logger.error('Invalid JSON received: %s', text_data)
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'Invalid JSON'}))
            except Exception as e:
                logger.error('Error processing message: %s', str(e))
                await self.send(text_data=json.dumps({'type': 'error', 'message': 'An unexpected error occurred'}))

    async def room_exists(self, room_id):
        return await self.room_service.get_room(room_id) is not None
