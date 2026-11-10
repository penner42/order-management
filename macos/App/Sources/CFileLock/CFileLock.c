#include "CFileLock.h"
#include <sys/file.h>

int order_manager_try_lock(int descriptor)
{
    return flock(descriptor, LOCK_EX | LOCK_NB);
}
