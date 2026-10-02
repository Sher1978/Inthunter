import sys

with open('src/services/swarm_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_block = '''                            ch.status = "FAILED"
                            ch.error_message = str(error)
                            await session.flush()
                            logger.warning(f"⚠️ Swarm Balancer: Channel {target_link} failed ({error}). Marked as FAILED to unblock queue.")
                    except Exception as join_err:
                        logger.warning(f"Notice auto-joining channel {target_link} during rebalance: {join_err}")'''

new_block = '''                            ch.status = "FAILED"
                            ch.error_message = str(error)
                            await session.flush()
                            logger.warning(f"⚠️ Swarm Balancer: Channel {target_link} failed ({error}). Marked as FAILED to unblock queue.")
                    except Exception as join_err:
                        err_str = str(join_err)
                        if 'StaleDataError' in err_str or 'rolled back' in err_str or 'expected to update' in err_str:
                            logger.warning(f"Concurrent delete detected for {target_link}. Aborting rebalance pass.")
                            await session.rollback()
                            return {"status": "aborted", "message": "Concurrent DB modification detected"}
                        logger.warning(f"Notice auto-joining channel {target_link} during rebalance: {join_err}")'''

if old_block in content:
    content = content.replace(old_block, new_block)
    with open('src/services/swarm_manager.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Patched swarm_manager.py flush block')
else:
    print('String not found in swarm_manager.py')
