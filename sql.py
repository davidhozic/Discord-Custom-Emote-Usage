from typing import List, Tuple
from sqlalchemy import (
                            Column,
                            ForeignKey,
                            Integer,
                            Date,
                            BigInteger,
                            String,
                            UniqueConstraint,
                            create_engine,
                            text,
                            or_,
                            and_
                       )
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy import func
import datetime as dt
import asyncio

sqlBase = declarative_base()


class Manager:
    """~ class ~
    @Info: Used for managing the sql database"""
    def __init__(self, filename) -> None:
        self.engine = None
        self.Session = None
        self.filename = filename
    
    def start(self):
        self.engine = create_engine(f"sqlite:///{self.filename}", echo=False)
        sqlBase.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        asyncio.create_task(self.update_history())

    def insert_emote_log(self, emotes, guild):
        session: Session
        with self.Session.begin() as session:
            for emote in emotes:
                # Add to Server table
                qserver: Server = session.query(Server).where(Server.snowflake == guild.id).first() # Server query
                if qserver is None:
                    qserver = Server(guild.name, guild.id)
                    session.add(qserver)
                    session.flush()

                # Add if it doesn't exists
                qemote: Emote = session.query(Emote).where(and_(or_(Emote.name == emote["name"], Emote.snowflake == emote["snowflake"]), Emote.server_id == qserver.id)).first() # Emote query
                if qemote is None:
                    qemote = Emote(emote["name"], emote["snowflake"], qserver.id)
                    session.add(qemote)
                    session.flush()
                else:
                    # Increase total count
                    qemote.name = emote["name"]
                    qemote.snowflake = emote["snowflake"]
                    qemote.total_count += 1

                # Increase daily counts
                qemote_daily: EmoteDaily = session.query(EmoteDaily).where(EmoteDaily.emote_id == qemote.id, EmoteDaily.timestamp == dt.datetime.now().date()).first()
                if qemote_daily is None:
                    qemote_daily = EmoteDaily(qemote.id)
                    session.add(qemote_daily)
                else:
                    qemote_daily.count += 1
              

    def statistics(self, server_snowflake: int, limit: int, day_limit: int, emote_snowflake: int=None, ascending=False) -> List[Tuple]:
        session: Session
        with self.Session.begin() as session:
            server: Server = session.query(Server).where(Server.snowflake == server_snowflake).first()
            if server is not None:
                conditions = [Emote.server_id == server.id, (dt.datetime.now() - EmoteDaily.timestamp) < day_limit]
                if emote_snowflake is not None:
                    conditions.append(Emote.snowflake == emote_snowflake)

                ret = (
                    session.query(Emote.name, Emote.snowflake, Emote.total_count, func.sum(EmoteDaily.count).label("count30day"))
                    .join(EmoteDaily, EmoteDaily.emote_id == Emote.id)
                    .where(*conditions)
                    .group_by(EmoteDaily.emote_id)
                    .order_by(text(f"count30day {'ASC' if ascending else 'DESC'}"))
                    .limit(limit)
                )
                all_ = ret.all()
                if all_ is not None:
                    return all_
        
        return []

    async def update_history(self):
        """~ coro ~ 
        @Info: Used for clearing daily logs that are older than days"""
        while True:
            current = dt.datetime.now()
            next = (current + dt.timedelta(days=1)).replace(hour=0, minute=0, second=1)
            await asyncio.sleep( (next-current).total_seconds() ) # Sleeps until midnight
            self.clear_old(30)

    def clear_old(self, days_old: int):
        """~ method ~
        @Info: Removes history that is older than days_old
        """
        with self.Session.begin() as session:
            session: Session
            session.execute(
                text(
                    f"""
                    DELETE FROM EmoteDaily
                    WHERE CAST(julianday() - julianday(timestamp) AS real) >= {days_old};
                    """
                )
            )
    

class Emote(sqlBase):
    """~ table descriptor class ~
    @Info: Used for tracking all the emotes in the server"""
    __tablename__ = "Emote"
    __table_args__= (UniqueConstraint("snowflake", "server_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    snowflake = Column(BigInteger)
    server_id = Column(Integer, ForeignKey("Server.id"))
    total_count = Column(BigInteger)

    def __init__(self, name, snowflake, server_id):
        self.name = name
        self.snowflake = snowflake
        self.server_id = server_id
        self.total_count = 1


class EmoteDaily(sqlBase):
    """~ table descriptor class ~
    @Info: Used for tracking daily usages"""
    __tablename__ = "EmoteDaily"
    emote_id  = Column(Integer, ForeignKey("Emote.id"), primary_key=True)
    count     = Column(Integer)
    timestamp = Column(Date, primary_key=True)

    def __init__(self, emote_id):
        self.emote_id = emote_id
        self.timestamp = dt.datetime.now().date()
        self.count = 1

class Server(sqlBase):
    """~ table descriptor class ~
    @Info: Used for tracking all the servers"""
    __tablename__ = "Server"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    snowflake = Column(BigInteger, unique=True)

    def __init__(self, name, snowflake) -> None:
        self.name = name
        self.snowflake = snowflake
