#ifndef ORDER_MANAGER_FILE_LOCK_H
#define ORDER_MANAGER_FILE_LOCK_H

// Returns 0 on acquisition, or -1 with errno set. Closing the descriptor releases
// the lock. Use flock rather than fcntl to match the .NET helper's FileShare.None.
int order_manager_try_lock(int descriptor);

#endif
