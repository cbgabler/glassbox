#include <string.h>
int check_password(const char *user, const char *expected) {
  return strcmp(user, expected) == 0;
}
