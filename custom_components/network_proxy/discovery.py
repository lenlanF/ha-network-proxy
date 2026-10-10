"""Bounded, opt-in RFC1918 scan with protocol handshake verification."""

import asyncio
import ipaddress

PRIVATE = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


async def discover(cidr):
    network = ipaddress.ip_network(cidr, strict=False)
    if (
        network.version != 4
        or network.num_addresses > 256
        or not any(network.subnet_of(n) for n in PRIVATE)
    ):
        raise ValueError("Choose an RFC1918 IPv4 subnet of at most 256 addresses")
    semaphore = asyncio.Semaphore(64)

    async def probe(host, port):
        async with semaphore:
            for protocol in ("socks5", "http"):
                writer = None
                try:
                    async with asyncio.timeout(1):
                        reader, writer = await asyncio.open_connection(str(host), port)
                        if protocol == "socks5":
                            writer.write(b"\x05\x01\x00")
                            await writer.drain()
                            valid = await reader.readexactly(2) == b"\x05\x00"
                        else:
                            writer.write(
                                b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
                            )
                            await writer.drain()
                            line = await reader.readline()
                            parts = line.split()
                            valid = (
                                len(parts) >= 2
                                and parts[0].startswith(b"HTTP/")
                                and parts[1] in (b"200", b"407")
                            )
                        if valid:
                            return f"{protocol}://{host}:{port}"
                except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError):
                    pass
                finally:
                    if writer:
                        writer.close()
                        try:
                            async with asyncio.timeout(0.5):
                                await writer.wait_closed()
                        except (OSError, TimeoutError):
                            pass
            return None

    results = await asyncio.gather(
        *(
            probe(host, port)
            for host in network.hosts()
            for port in (7890, 7891, 7893, 1080, 8080)
        )
    )
    return sorted({result for result in results if result})
